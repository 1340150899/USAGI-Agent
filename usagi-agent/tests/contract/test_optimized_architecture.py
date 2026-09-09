from __future__ import annotations

import asyncio
import pytest

from examples.structured_agent.agent import create_research_writer_agent
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.api.errors import UnknownToolError
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import ServiceRuntimeInitializer
from usagi_agent.tools import ToolAdapter, ToolSpec, to_model_tool
from usagi_agent.ports import ToolContext
from usagi_agent.prompts import RESEARCH_WRITER_PROMPT, prompt_for_agent


class _FailingLifecycleTool(ToolAdapter):
    spec = ToolSpec(name="failing_lifecycle", description="test", parameters={})

    async def execute(self, arguments, context: ToolContext):
        return {}

    async def health(self):
        raise RuntimeError("health failure")

    async def shutdown(self):
        raise RuntimeError("shutdown failure")


class _CloseProbe:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _HealthFailureProbe:
    async def health(self):
        raise RuntimeError("persistence health failure")


def test_tool_spec_is_the_minimal_function_contract():
    # The model-facing contract stays three fields; the extra ToolSpec fields
    # are execution metadata consumed by ToolRuntime, never by the model.
    spec = SearchToolAdapter.spec
    function = to_model_tool(spec)["function"]
    assert isinstance(function, dict)
    assert set(function) == {"name", "description", "parameters"}
    assert function["name"] == "web_search"
    for field in (
        "risk", "write_safety", "timeout_seconds", "max_concurrency",
        "max_retries", "retry_backoff_seconds", "max_output_bytes",
        "adapter_kind", "execution_env", "required_scopes",
    ):
        assert field in ToolSpec.model_fields
    assert ToolSpec(name="t", description="d").risk == "read"


def test_service_init_registers_only_builtins_and_does_not_compile_scenarios(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    assert set(spec.name for spec in runtime.tool_manager.all_specs()) == {
        "current_time", "calculator", "artifact_reader"
    }
    assert len(runtime.scenario_registry) == 0
    with pytest.raises(UnknownToolError):
        runtime.tool_manager.resolve("web_search")
    asyncio.run(runtime.shutdown())


def test_pipeline_init_reuses_service_instances_and_scenario_has_no_tools(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    persistence = runtime.persistence
    runtime.tool_manager.register(SearchToolAdapter())
    create_research_writer_agent(runtime.agent_manager)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    scenario = runtime.scenario_registry.get("example.research_writer")
    assert runtime.persistence is persistence
    assert "tools" not in scenario.config.model_dump()
    stages = (
        scenario.config.pipeline.pre_recall,
        scenario.config.pipeline.recall,
        scenario.config.pipeline.context_build.filters,
        scenario.config.pipeline.context_build.rankers,
        scenario.config.pipeline.model,
        scenario.config.pipeline.result_process,
        scenario.config.pipeline.end,
    )
    for stage in stages:
        for adapter in stage:
            assert "version" not in type(adapter).model_fields
            assert adapter.name and adapter.type
    asyncio.run(runtime.shutdown())


def test_scenario_uses_only_framework_owned_stage_rules():
    for stage in (
        SCENARIO_CONFIGS[0].pipeline.pre_recall,
        SCENARIO_CONFIGS[0].pipeline.recall,
        SCENARIO_CONFIGS[0].pipeline.context_build.filters,
        SCENARIO_CONFIGS[0].pipeline.context_build.rankers,
        SCENARIO_CONFIGS[0].pipeline.model,
        SCENARIO_CONFIGS[0].pipeline.result_process,
        SCENARIO_CONFIGS[0].pipeline.end,
    ):
        assert all(type(rule).__module__.startswith("usagi_agent.pipelines.rules") for rule in stage)


def test_prompt_is_resolved_from_static_catalog_not_agent_or_manager_state(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    registered = create_research_writer_agent(runtime.agent_manager)
    assert "prompt" not in type(registered).model_fields
    assert prompt_for_agent(registered.id) is RESEARCH_WRITER_PROMPT
    assert not hasattr(runtime.agent_manager, "render_prompt")
    asyncio.run(runtime.shutdown())


def test_health_reports_every_persistence_resource_and_isolates_failures(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    runtime.tool_manager.register(_FailingLifecycleTool())
    runtime.persistence.event_bus = _HealthFailureProbe()
    report = asyncio.run(runtime.health())
    assert not report.healthy
    assert report.components["tool:failing_lifecycle"] == "unhealthy"
    assert report.components["persistence:event_bus"] == "unhealthy"
    persistence_components = {
        name for name in report.components if name.startswith("persistence:")
    }
    assert len(persistence_components) == len(
        {id(component) for component in vars(runtime.persistence).values()}
    )
    with pytest.raises(ExceptionGroup):
        asyncio.run(runtime.shutdown())


def test_shutdown_continues_after_an_earlier_resource_fails(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(model_execution_mode="scripted", sqlite_path=str(tmp_path / "runtime.db"))
    )
    runtime.tool_manager.register(_FailingLifecycleTool())
    probe = _CloseProbe()
    runtime.persistence.event_bus = probe
    with pytest.raises(ExceptionGroup):
        asyncio.run(runtime.shutdown())
    assert probe.closed
