"""Compile one fixed six-stage business pipeline into a LangGraph graph."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from itertools import pairwise
import logging
import time
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from usagi_agent.kernel.context import RunContext
from usagi_agent.observability import operation
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processor import PipelineProcessor
from usagi_agent.pipelines.rules.stage import PipelineStagePatch
from usagi_agent.scenarios.config import ScenarioConfig

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime

StageProcess = Callable[[AgentRunState, RunContext], Awaitable[PipelineStagePatch]]
log = logging.getLogger(__name__)


def _wrap_stage(
    process: StageProcess,
    runtime: ServerRuntime,
    *,
    budget,
    stage_name: str,
    scenario_key: str,
) -> Callable[..., Awaitable[dict]]:
    async def node(state: AgentRunState, config: RunnableConfig) -> dict:
        context = RunContext.from_graph_config(config)
        started = time.monotonic()
        log.info("pipeline_stage_started run_id=%s stage=%s", context.run_id, stage_name)
        configurable = config.get("configurable", {})
        try:
            with operation(
                runtime.observability,
                "pipeline.stage",
                **{
                    "usagi.pipeline.stage.name": stage_name,
                    "usagi.pipeline.iteration": state.get("iteration", 0) + 1,
                    "usagi.scenario.name": scenario_key,
                },
            ) as telemetry:
                components = runtime.kernel_components
                if components is not None:
                    await components.middleware.before_node(
                        context.run_id, context.tenant_id,
                        configurable.get("fencing_gate"), context.deadline, budget,
                    )
                result = await process(state, context)
                disposition = result.get("pass_disposition") if isinstance(result, dict) else None
                if disposition == "run_failed":
                    telemetry.set_outcome("failed")
        except Exception as exc:
            log.exception("pipeline_stage_failed run_id=%s stage=%s error_type=%s", context.run_id, stage_name, type(exc).__name__)
            raise
        log.info("pipeline_stage_completed run_id=%s stage=%s duration_ms=%.3f", context.run_id, stage_name, (time.monotonic() - started) * 1000)
        if not isinstance(result, dict):
            raise TypeError("pipeline stage must return dict")
        return dict(result)

    return node


class PipelineCompiler:
    def __init__(self, state_schema: type = AgentRunState) -> None:
        self._state_schema = state_schema

    def compile(
        self,
        scenario: ScenarioConfig,
        runtime: ServerRuntime,
        *,
        checkpointer=None,
    ) -> Any:
        pipeline = scenario.pipeline
        processor = PipelineProcessor(scenario, runtime)
        stages: tuple[tuple[str, StageProcess], ...] = (
            ("pre_recall", processor.process_pre_recall),
            ("recall", processor.process_recall),
            ("context_build", processor.process_context_build),
            ("model", processor.process_model),
            ("result_process", processor.process_result),
            ("end", processor.process_end),
        )
        graph: StateGraph = StateGraph(self._state_schema)
        for name, method in stages:
            # Tool-call budget enforcement lives inside the ResultProcess
            # stage (after action formation, before execution).
            graph.add_node(
                name,
                _wrap_stage(
                    method,
                    runtime,
                    budget=pipeline.budget,
                    stage_name=name,
                    scenario_key=scenario.key,
                ),
            )
        graph.add_edge(START, stages[0][0])
        for (source, _), (target, _) in pairwise(stages):
            graph.add_edge(source, target)

        async def next_pass(state: AgentRunState) -> str:
            if (
                state.get("pass_disposition") == "next_pass"
                and state.get("iteration", 0) < pipeline.max_passes
            ):
                return "pre_recall"
            return END

        graph.add_conditional_edges(
            "end", next_pass, {"pre_recall": "pre_recall", END: END}
        )
        kwargs = {"checkpointer": checkpointer} if checkpointer is not None else {}
        return graph.compile(**kwargs)
