"""Compile one fixed six-stage business pipeline into a LangGraph graph."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableConfig

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.scenarios.config import ScenarioConfig

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime

StageMethod = Callable[[AgentRunState, "ServerRuntime", RunContext], Awaitable[dict[str, object]]]


def _wrap_stage(
    method: StageMethod,
    runtime: "ServerRuntime",
    *,
    budget,
    limits: tuple[int, int] | None = None,
) -> Callable[..., Awaitable[dict]]:
    async def node(state: AgentRunState, config: RunnableConfig) -> dict:
        context = RunContext.from_graph_config(config)
        configurable = config.get("configurable", {})
        components = runtime.kernel_components
        if components is not None:
            await components.middleware.before_node(
                context.run_id,
                context.tenant_id,
                configurable.get("fencing_gate"),
                context.deadline,
                budget,
            )
        tool_count = state.get("tool_call_count", 0)
        delegation_count = state.get("delegation_count", 0)
        if limits is not None:
            if state.get("action_type") == "tool" and tool_count >= limits[0]:
                return {"pass_disposition": "run_failed"}
            if state.get("action_type") == "delegate" and delegation_count >= limits[1]:
                return {"pass_disposition": "run_failed"}
        result = await method(state, runtime, context)
        if not isinstance(result, dict):
            raise TypeError("pipeline stage must return dict")
        if limits is not None:
            if state.get("action_type") == "tool":
                tool_count += 1
                result["tool_call_count"] = tool_count
            elif state.get("action_type") == "delegate":
                delegation_count += 1
                result["delegation_count"] = delegation_count
        return result

    return node


class PipelineCompiler:
    def __init__(self, state_schema: type = AgentRunState) -> None:
        self._state_schema = state_schema

    def compile(self, scenario: ScenarioConfig, runtime: "ServerRuntime", *, checkpointer=None) -> Any:
        pipeline = scenario.pipeline
        stages: tuple[tuple[str, StageMethod], ...] = (
            ("pre_recall", pipeline.pre_recall.pre_recall),
            ("recall", pipeline.recall.recall),
            ("context_build", pipeline.context_build.build_context),
            ("model", pipeline.model.model),
            ("result_process", pipeline.result_process.process_result),
            ("end", pipeline.end.end),
        )
        graph: StateGraph = StateGraph(self._state_schema)
        for name, method in stages:
            limits = (pipeline.max_tool_calls, pipeline.max_delegations) if name == "end" else None
            graph.add_node(
                name, _wrap_stage(method, runtime, budget=pipeline.budget, limits=limits)
            )
        graph.add_edge(START, stages[0][0])
        for (source, _), (target, _) in zip(stages, stages[1:]):
            graph.add_edge(source, target)

        async def next_pass(state: AgentRunState) -> str:
            if state.get("pass_disposition") == "next_pass" and state.get("iteration", 0) < pipeline.max_passes:
                return "pre_recall"
            return END

        graph.add_conditional_edges("end", next_pass, {"pre_recall": "pre_recall", END: END})
        kwargs = {"checkpointer": checkpointer} if checkpointer is not None else {}
        return graph.compile(**kwargs)
