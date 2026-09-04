from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import RecallAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    RecallRuleInput,
    RecallRuleOutput,
    RuleExecutionError,
    RecallStagePatch,
)

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class RecallProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[RecallAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules

    async def process(
        self, state: AgentRunState, context: RunContext
    ) -> RecallStagePatch:
        rule_input = RecallRuleInput(
            normalized_input_ref=state.get("normalized_input_ref", ""),
            recall_plan_ref=state.get("recall_plan_ref", ""),
            recall_cache=state.get("recall_cache", {}),
        )
        stage_patch: RecallStagePatch = {}
        for config in self.rules:
            result = await config.recall(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, RecallRuleOutput):
                raise TypeError("recall rule returned an invalid output")
            changes = result.model_dump(exclude_none=True)
            if result.recall_cache is not None:
                stage_patch["recall_cache"] = result.recall_cache
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch
