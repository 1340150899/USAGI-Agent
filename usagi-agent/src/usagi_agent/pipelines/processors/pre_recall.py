from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_text, put_side_effect_receipt
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
        normalized_ref = state.get("normalized_input_ref", "")
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
        if not normalized_ref:
            normalized_ref = state.get("request_ref", "")
            content = await get_text(
                self.runtime.persistence.artifact_manager, normalized_ref
            )
            operation_id = f"memory:user-event:{context.run_id}"
            event = await self.runtime.memory_manager.append_event(
                session_id=context.thread_id,
                role="user",
                content=content,
                ctx=context.to_tool_context(),
                metadata={"run_id": context.run_id},
                operation_id=operation_id,
            )
            receipt_ref = await put_side_effect_receipt(
                self.runtime.persistence.artifact_manager,
                operation_id=operation_id,
                effect_type="memory.append_event",
                result_ref=event.event_id,
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
            )
            stage_patch["normalized_input_ref"] = normalized_ref
            stage_patch["side_effect_receipt_refs"] = [receipt_ref]
        rule_input = PreRecallRuleInput(
            request_ref=state.get("request_ref", ""),
            normalized_input_ref=normalized_ref,
            recall_plan_ref="",
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
            if result.normalized_input_ref is not None:
                stage_patch["normalized_input_ref"] = result.normalized_input_ref
            if result.recall_plan_ref is not None:
                stage_patch["recall_plan_ref"] = result.recall_plan_ref
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch
