from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_text
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import PreRecallAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    PreRecallRuleInput,
    PreRecallRuleOutput,
    RuleExecutionError,
    StatePatch,
)

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class PreRecallProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[PreRecallAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules

    async def process(self, state: AgentRunState, context: RunContext) -> StatePatch:
        normalized_ref = state.get("normalized_input_ref", "")
        stage_patch: StatePatch = {}
        if not normalized_ref:
            normalized_ref = state.get("request_ref", "")
            content = await get_text(
                self.runtime.persistence.artifact_manager, normalized_ref
            )
            await self.runtime.memory_manager.append_event(
                session_id=context.thread_id,
                role="user",
                content=content,
                ctx=context.to_tool_context(),
                metadata={"run_id": context.run_id},
            )
            stage_patch["normalized_input_ref"] = normalized_ref
        rule_input = PreRecallRuleInput(
            request_ref=state.get("request_ref", ""),
            normalized_input_ref=normalized_ref,
            recall_plan_ref=state.get("recall_plan_ref", ""),
        )
        for config in self.rules:
            result = await config.pre_recall(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, PreRecallRuleOutput):
                raise TypeError("pre_recall rule returned an invalid output")
            changes = result.model_dump(exclude_none=True)
            stage_patch.update(changes)
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch
