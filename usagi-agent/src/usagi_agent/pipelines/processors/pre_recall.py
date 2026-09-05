from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import PreRecallAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    PreRecallRuleInput,
    PreRecallRuleOutput,
    RuleExecutionError,
    PreRecallStagePatch,
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

    async def process(
        self, state: AgentRunState, context: RunContext
    ) -> PreRecallStagePatch:
        # Explicitly clear every pass-scoped field before producing this pass.
        stage_patch: PreRecallStagePatch = {
            "recall_plan_ref": "",
            "recall_cache": {},
            "context_pack_ref": "",
            "model_request_ref": "",
            "context_operation": "normal",
            "model_response_ref": "",
            "action_type": "",
            "action_hash": "",
            "agent_action_ref": "",
            "tool_action_refs": [],
            "pass_disposition": "",
        }
        rule_input = PreRecallRuleInput(
            request_ref=state.get("request_ref", ""),
            recall_plan_ref="",
            iteration=state.get("iteration", 0),
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
            if result.recall_plan_ref is not None:
                stage_patch["recall_plan_ref"] = result.recall_plan_ref
            if result.context_compaction_mode is not None:
                stage_patch["context_compaction_mode"] = (
                    result.context_compaction_mode
                )
            if result.side_effect_receipt_refs:
                stage_patch.setdefault("side_effect_receipt_refs", []).extend(
                    result.side_effect_receipt_refs
                )
            rule_input = rule_input.model_copy(
                update={
                    key: value
                    for key, value in changes.items()
                    if key == "recall_plan_ref"
                }
            )
        return stage_patch
