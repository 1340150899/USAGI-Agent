from __future__ import annotations

import asyncio
import pytest

from examples.structured_agent.agent import RESEARCH_WRITER_AGENT
from examples.structured_agent.configs import SCENARIO_CONFIGS
from examples.structured_agent.model_adapter import ScriptedModelAdapter
from examples.structured_agent.tools import SearchToolAdapter
from usagi_agent.api.errors import UnknownToolError
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import ServiceRuntimeInitializer
from usagi_agent.tools import ToolAdapter, ToolSpec, to_model_tool
from usagi_agent.ports import ToolContext


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
    assert set(ToolSpec.model_fields) == {"name", "description", "parameters"}
    spec = SearchToolAdapter.spec
    assert to_model_tool(spec)["function"]["name"] == "web_search"


def test_service_init_registers_only_builtins_and_does_not_compile_scenarios():
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=ScriptedModelAdapter()
    )
    assert set(spec.name for spec in runtime.tool_manager.all_specs()) == {
        "current_time", "calculator", "artifact_reader"
    }
    assert len(runtime.scenario_registry) == 0
    with pytest.raises(UnknownToolError):
        runtime.tool_manager.resolve("web_search")
    asyncio.run(runtime.shutdown())


def test_pipeline_init_reuses_service_instances_and_scenario_has_no_tools():
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=ScriptedModelAdapter()
    )
    persistence = runtime.persistence
    runtime.tool_manager.register(SearchToolAdapter())
    runtime.agent_manager.register(RESEARCH_WRITER_AGENT)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    scenario = runtime.scenario_registry.get("example.research_writer")
    assert runtime.persistence is persistence
    assert "tools" not in scenario.config.model_dump()
    for adapter in (
        scenario.config.pipeline.pre_recall,
        scenario.config.pipeline.recall,
        scenario.config.pipeline.context_build,
        scenario.config.pipeline.model,
        scenario.config.pipeline.result_process,
        scenario.config.pipeline.end,
    ):
        assert "version" not in type(adapter).model_fields
        assert adapter.name and adapter.type
    asyncio.run(runtime.shutdown())


def test_agent_manager_owns_and_renders_prompts():
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=ScriptedModelAdapter()
    )
    runtime.agent_manager.register(RESEARCH_WRITER_AGENT)
    prompt_ref, template = runtime.agent_manager.get_prompt("research_writer")
    assert prompt_ref == RESEARCH_WRITER_AGENT.prompt
    assert runtime.agent_manager.render_prompt("research_writer") == template
    asyncio.run(runtime.shutdown())


def test_health_reports_every_persistence_resource_and_isolates_failures():
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=ScriptedModelAdapter()
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


def test_shutdown_continues_after_an_earlier_resource_fails():
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(), model_adapter=ScriptedModelAdapter()
    )
    runtime.tool_manager.register(_FailingLifecycleTool())
    probe = _CloseProbe()
    runtime.persistence.event_bus = probe
    with pytest.raises(ExceptionGroup):
        asyncio.run(runtime.shutdown())
    assert probe.closed
