"""Business-only scenario validation, compilation, and registration."""
from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterable

from usagi_agent.api.errors import BundleValidationError
from usagi_agent.pipelines.rules import StageType
from usagi_agent.scenarios import ScenarioConfig, ScenarioRuntime


def _stage_source(adapter: object) -> str:
    try:
        return inspect.getsource(type(adapter))
    except (OSError, TypeError):
        return f"{type(adapter).__module__}.{type(adapter).__qualname__}"


class ScenarioPipelineInitializer:
    @staticmethod
    def _context_build_adapters(config) -> tuple[object, ...]:
        return (*config.filters, *config.rankers)

    @staticmethod
    def init(runtime, scenarios: Iterable[ScenarioConfig]) -> tuple[ScenarioRuntime, ...]:
        scenario_list = tuple(scenarios)
        keys = [scenario.key for scenario in scenario_list]
        if len(keys) != len(set(keys)):
            raise BundleValidationError("duplicate scenario keys in initialization batch")
        registered = set(runtime.scenario_registry.keys())
        duplicate = registered.intersection(keys)
        if duplicate:
            raise BundleValidationError(f"scenario already registered: {sorted(duplicate)[0]}")
        for scenario in scenario_list:
            ScenarioPipelineInitializer._validate(runtime, scenario)

        built: list[ScenarioRuntime] = []
        for scenario in scenario_list:
            graph = runtime.pipeline_compiler.compile(
                scenario, runtime, checkpointer=runtime.persistence.checkpointer
            )
            agent = runtime.agent_manager.get(scenario.agent_id)
            tool_specs = runtime.tool_manager.get_specs(agent.allowed_tools)
            stages = (
                scenario.pipeline.pre_recall,
                scenario.pipeline.recall,
                ScenarioPipelineInitializer._context_build_adapters(
                    scenario.pipeline.context_build
                ),
                scenario.pipeline.model,
                scenario.pipeline.result_process,
                scenario.pipeline.end,
            )
            adapters = tuple(adapter for stage in stages for adapter in stage)
            definition = {
                "scenario": scenario.model_dump(mode="json"),
                "agent": agent.model_dump(mode="json"),
                "tools": [spec.model_dump(mode="json") for spec in tool_specs],
                "stage_classes": [
                    f"{type(adapter).__module__}.{type(adapter).__qualname__}" for adapter in adapters
                ],
                "stage_sources": [_stage_source(adapter) for adapter in adapters],
            }
            serialized = json.dumps(definition, sort_keys=True, separators=(",", ":"))
            checksum = hashlib.sha256(serialized.encode()).hexdigest()
            item = ScenarioRuntime(
                key=scenario.key, config=scenario, compiled_graph=graph, checksum=checksum
            )
            built.append(item)
        for item in built:
            runtime.scenario_registry.register(item)
        return tuple(built)

    @staticmethod
    def _validate(runtime, scenario: ScenarioConfig) -> None:
        if not scenario.key.strip():
            raise BundleValidationError("scenario key must not be empty")
        agent = runtime.agent_manager.get(scenario.agent_id)
        runtime.tool_manager.get_specs(agent.allowed_tools)
        stages = (
            (scenario.pipeline.pre_recall, StageType.PRE_RECALL),
            (scenario.pipeline.recall, StageType.RECALL),
            (scenario.pipeline.context_build.filters, StageType.CONTEXT_BUILD),
            (scenario.pipeline.context_build.rankers, StageType.CONTEXT_BUILD),
            (scenario.pipeline.model, StageType.MODEL),
            (scenario.pipeline.result_process, StageType.RESULT_PROCESS),
            (scenario.pipeline.end, StageType.END),
        )
        names: set[str] = set()
        for adapters, expected_type in stages:
            for adapter in adapters:
                if adapter.type != expected_type:
                    raise BundleValidationError(
                        f"stage rule {adapter.name!r} has type {adapter.type!r}; "
                        f"expected {expected_type!r}"
                    )
                if not adapter.name.strip():
                    raise BundleValidationError(
                        f"{expected_type} stage rule name must not be empty"
                    )
                if adapter.name in names:
                    raise BundleValidationError(f"duplicate stage rule name: {adapter.name}")
                names.add(adapter.name)
