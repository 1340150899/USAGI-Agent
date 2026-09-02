"""LangGraph-facing facade for the six fixed pipeline stages."""
from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors import (
    ContextBuildProcessor,
    EndProcessor,
    ModelProcessor,
    PreRecallProcessor,
    RecallProcessor,
    ResultProcessProcessor,
)
from usagi_agent.pipelines.rules.stage import StatePatch

if TYPE_CHECKING:
    from usagi_agent.scenarios.config import ScenarioConfig
    from usagi_agent.server.runtime import ServerRuntime


class PipelineProcessor:
    """Keep graph node APIs stable while delegating stage-specific behavior."""

    def __init__(
        self,
        scenario: "ScenarioConfig",
        runtime: "ServerRuntime",
    ) -> None:
        pipeline = scenario.pipeline
        agent = runtime.agent_manager.get(scenario.agent_id)
        self._pre_recall = PreRecallProcessor(pipeline.pre_recall, runtime, agent)
        self._recall = RecallProcessor(pipeline.recall, runtime, agent)
        self._context_build = ContextBuildProcessor(
            pipeline.context_build, runtime, agent
        )
        self._model = ModelProcessor(pipeline.model, runtime, agent)
        self._result_process = ResultProcessProcessor(
            pipeline.result_process, runtime, agent
        )
        self._end = EndProcessor(pipeline.end, runtime, agent)

    async def process_pre_recall(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        return await self._pre_recall.process(state, context)

    async def process_recall(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        return await self._recall.process(state, context)

    async def process_context_build(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        return await self._context_build.process(state, context)

    async def process_model(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        return await self._model.process(state, context)

    async def process_result(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        return await self._result_process.process(state, context)

    async def process_end(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        return await self._end.process(state, context)
