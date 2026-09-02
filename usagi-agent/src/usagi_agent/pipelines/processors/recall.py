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
    StatePatch,
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

    async def process(self, state: AgentRunState, context: RunContext) -> StatePatch:
        rule_input = RecallRuleInput(
            normalized_input_ref=state.get("normalized_input_ref", ""),
            recall_plan_ref=state.get("recall_plan_ref", ""),
            recall_cache=state.get("recall_cache", {}),
            recall_bundle_ref=state.get("recall_bundle_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.rules:
            result = await config.recall(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, RecallRuleOutput):
                raise TypeError("recall rule returned an invalid output")
            changes = result.model_dump(exclude_none=True)
            stage_patch.update(changes)
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch
