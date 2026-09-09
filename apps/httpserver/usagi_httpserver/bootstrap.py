"""Application-owned runtime assembly. No per-request registration."""

from pathlib import Path

from pydantic import SecretStr
from usagi_agent.models import DEFAULT_MODEL, GLM_5_3_FLASH_CODING_PLAN_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.tools import MCPServerConfig

from .tool_specs import PYTHON_TOOL_SPECS, XHS_TOOL_SPECS


def _bootstrap_settings(settings: dict, data: Path, key: str) -> BootstrapSettings:
    """Translate application JSON settings into framework bootstrap settings."""
    return BootstrapSettings(
        sqlite_dev_path=str(data / "runtime-dev.db"),
        sqlite_debug_path=str(data / "runtime-debug.db"),
        database_environment=settings.get("database_environment", "dev"),
        resume_hmac_key=SecretStr(key),
        model_execution_mode=settings.get("model_execution_mode", "live"),
        service_name=settings.get("otel_service_name", "usagi-httpserver"),
        service_version=settings.get("service_version", "0.1.0"),
        service_instance_id=settings.get("service_instance_id", "httpserver-1"),
        deployment_environment=settings.get("deployment_environment", "dev"),
        otel_exporter=settings.get("otel_exporter", "none"),
        otel_endpoint=settings.get("otel_endpoint"),
        otel_trace_sample_ratio=settings.get("otel_trace_sample_ratio", 1.0),
        otel_metric_export_interval_millis=settings.get(
            "otel_metric_export_interval_millis", 60_000
        ),
    )


async def build_server(*, xhs_url: str | None = None, settings=None):
    settings = settings or {}
    profiles = {
        "chat": DEFAULT_MODEL,
        "coding_plan": GLM_5_3_FLASH_CODING_PLAN_MODEL,
    }
    profile = settings.get("model_profile", "chat")
    if profile not in profiles:
        raise ValueError("model_profile must be chat or coding_plan")
    data = Path(settings.get("data_dir", ".usagi/http")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    key = settings.get("resume_hmac_key", "")
    if len(key) < 32:
        raise ValueError("resume_hmac_key must contain at least 32 characters")
    runtime = ServiceRuntimeInitializer.init(
        _bootstrap_settings(settings, data, key),
        tool_specs=PYTHON_TOOL_SPECS,
    )
    try:
        if xhs_url:
            await runtime.tool_manager.register_mcp(
                MCPServerConfig(
                    name="xhs",
                    name_prefix="",
                    transport="streamable_http",
                    url=xhs_url,
                    enabled_tools=tuple(XHS_TOOL_SPECS),
                    spec_overrides=XHS_TOOL_SPECS,
                    read_timeout_seconds=300,
                )
            )
        runtime.agent_manager.create_agent(
            id="research_writer",
            input_schema="application.request@1.0.0",
            output_schema="usagi.final_output@1.0.0",
            model=profiles[profile],
            allowed_tools=tuple(s.name for s in runtime.tool_manager.all_specs()),
        )
        ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
        return Server(runtime)
    except BaseException:
        await runtime.shutdown()
        raise
