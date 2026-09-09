"""Application-owned runtime assembly. No per-request registration."""
from usagi_agent.models import GLM_5_3_FLASH_MODEL, GLM_5_3_FLASH_CODING_PLAN_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.tools import MCPServerConfig
from pydantic import SecretStr
from pathlib import Path

from .tool_specs import PYTHON_TOOL_SPECS, XHS_TOOL_SPECS


async def build_server(*, xhs_url: str | None = None, settings=None):
    settings=settings or {}
    profiles={'chat':GLM_5_3_FLASH_MODEL,'coding_plan':GLM_5_3_FLASH_CODING_PLAN_MODEL}
    profile=settings.get('model_profile','chat')
    if profile not in profiles:
        raise ValueError('model_profile must be chat or coding_plan')
    data=Path(settings.get('data_dir','.usagi/http')).resolve()
    data.mkdir(parents=True,exist_ok=True)
    key=settings.get('resume_hmac_key','')
    if len(key)<32:
        raise ValueError('resume_hmac_key must contain at least 32 characters')
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            sqlite_dev_path=str(data/'runtime-dev.db'),
            sqlite_debug_path=str(data/'runtime-debug.db'),
            database_environment=settings.get('database_environment','dev'),
            resume_hmac_key=SecretStr(key),
        ),
        tool_specs=PYTHON_TOOL_SPECS,
    )
    try:
        if xhs_url:
            await runtime.tool_manager.register_mcp(MCPServerConfig(
                name="xhs", name_prefix="", transport="streamable_http", url=xhs_url,
                enabled_tools=tuple(XHS_TOOL_SPECS), spec_overrides=XHS_TOOL_SPECS,
                read_timeout_seconds=300,
            ))
        runtime.agent_manager.create_agent(
            id="research_writer", input_schema="application.request@1.0.0",
            output_schema="usagi.final_output@1.0.0", model=profiles[profile],
            allowed_tools=tuple(s.name for s in runtime.tool_manager.all_specs()),
        )
        ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
        return Server(runtime)
    except BaseException:
        await runtime.shutdown()
        raise
