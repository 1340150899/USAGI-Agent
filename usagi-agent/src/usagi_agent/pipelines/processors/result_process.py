"""ResultProcess stage: form AgentActions, then run the rule chain.

The stage's fixed logic interprets the model response and persists one
artifact per proposed action (design §19); rules then react to the formed
action — the framework ships ToolExecutionRule (governed execution) and
CompactionApplyRule (memory mutation). Rules receive an immutable snapshot
of the formation result and run serially, which keeps the contract
parallel-ready: no rule observes another rule's output.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import (
    ModelContextEnvelope,
    get_model,
    put_bytes,
    put_model,
    put_side_effect_receipt,
)
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import ResultProcessAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
    RuleExecutionError,
    ResultProcessStagePatch,
)
from usagi_agent.types.action import (
    FailureAction,
    FinalAction,
    SafeErrorRecord,
    ToolAction,
)
from usagi_agent.types.context import (
    CompactionResult,
    ContextUpdate,
    LongTermMemoryCandidate,
)
from usagi_agent.types.content import merge_content_parts
from usagi_agent.types.model import ModelResponse, ModelToolCall
from usagi_agent.types.refs import ArtifactRef

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class ResultProcessProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[ResultProcessAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
        *,
        max_tool_calls: int = 10,
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules
        self.max_tool_calls = max_tool_calls

    async def process(
        self, state: AgentRunState, context: RunContext
    ) -> ResultProcessStagePatch:
        rule_input = ResultProcessRuleInput(
            model_response_ref=state.get("model_response_ref", ""),
            context_pack_ref=state.get("context_pack_ref", ""),
            context_operation=state.get("context_operation", "normal"),
            iteration=state.get("iteration", 0),
        )
        stage_patch = await self._form_action(rule_input, context)
        rule_input = rule_input.model_copy(
            update={
                "action_type": str(stage_patch.get("action_type", "")),
                "action_hash": str(stage_patch.get("action_hash", "")),
                "agent_action_ref": str(stage_patch.get("agent_action_ref", "")),
                "tool_action_refs": tuple(
                    cast(
                        "tuple[str, ...]",
                        stage_patch.get("tool_action_refs") or (),
                    )
                ),
            }
        )
        # Tool-call budget gate: stop before any rule gets to execute.
        if stage_patch.get("action_type") == "tool":
            used = int(state.get("tool_call_count", 0))
            calls = len(rule_input.tool_action_refs)
            if used + calls > self.max_tool_calls:
                stage_patch["pass_disposition"] = "run_failed"
                stage_patch["reason_codes"] = ["tool.budget_exceeded"]
                return stage_patch
            stage_patch["tool_call_count"] = used + calls
        for config in self.rules:
            result = await config.process_result(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None or isinstance(result, RuleExecutionError):
                continue
            if not isinstance(result, ResultProcessRuleOutput):
                raise TypeError("result_process rule returned an invalid output")
            if result.tool_observation_refs:
                merged = [
                    *cast(
                        "list[str]",
                        stage_patch.get("tool_observation_refs") or [],
                    ),
                    *result.tool_observation_refs,
                ]
                stage_patch["tool_observation_refs"] = list(dict.fromkeys(merged))
            if result.reason_codes:
                merged_codes = [
                    *cast("list[str]", stage_patch.get("reason_codes") or []),
                    *result.reason_codes,
                ]
                stage_patch["reason_codes"] = list(dict.fromkeys(merged_codes))
            if result.side_effect_receipt_refs:
                merged_receipts = [
                    *cast(
                        "list[str]",
                        stage_patch.get("side_effect_receipt_refs") or [],
                    ),
                    *result.side_effect_receipt_refs,
                ]
                stage_patch["side_effect_receipt_refs"] = list(
                    dict.fromkeys(merged_receipts)
                )
            if result.pass_disposition is not None:
                stage_patch["pass_disposition"] = result.pass_disposition
                break
        return stage_patch

    # --------------------------------------------------------------- formation

    async def _form_action(
        self, input: ResultProcessRuleInput, context: RunContext
    ) -> ResultProcessStagePatch:
        """Formation: interpret the model response into persisted actions.

        Pure interpretation plus action-artifact persistence — the turn's
        memory side effects live in _record_model_turn so parsing and
        mutation stay separately readable.
        """
        artifact_manager = self.runtime.persistence.artifact_manager
        response = await get_model(
            artifact_manager, input.model_response_ref, ModelResponse
        )
        if input.context_operation == "compaction":
            return await self._form_compaction(response, input, context)
        response, context_update, _ = await self._interpret_response(
            response, input, context
        )
        tool_actions = self._form_tool_actions(response.tool_calls) if response else []
        receipt_refs = await self._record_model_turn(
            response, context_update, tool_actions, input.iteration, context
        )
        tool_action_refs: list[str] = []
        action: FailureAction | FinalAction | None = None
        if response is None:
            action = FailureAction(
                error=SafeErrorRecord(reason_code="model.no_response", message="missing"),
                source_stage="result_process",
            )
            action_type = "failure"
        elif tool_actions:
            action_type = "tool"
        elif response.content_ref is not None:
            action = FinalAction(
                output_ref=response.content_ref,
                output_schema="usagi.final_output@1.0.0",
            )
            action_type = "final"
        else:
            action = FailureAction(
                error=SafeErrorRecord(
                    reason_code="model.empty_response", message="missing model content"
                ),
                source_stage="result_process",
            )
            action_type = "failure"
        if action_type == "tool":
            assert response is not None
            for call in tool_actions:
                tool_action_refs.append(
                    await put_model(
                        artifact_manager,
                        call,
                        tenant_id=context.tenant_id,
                        scope_id=context.run_id,
                        operation_id=(
                            f"action:{context.run_id}:{input.iteration}:"
                            f"{call.tool_call_id}"
                        ),
                    )
                )
            payload = json.dumps(
                [call.model_dump(mode="json") for call in tool_actions],
                sort_keys=True,
            )
            return {
                "action_type": "tool",
                "action_hash": hashlib.sha256(payload.encode()).hexdigest(),
                "agent_action_ref": tool_action_refs[0],
                "tool_action_refs": tool_action_refs,
                "side_effect_receipt_refs": receipt_refs,
            }
        assert action is not None
        ref = await put_model(
            artifact_manager,
            action,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"action:{context.run_id}:{input.iteration}",
        )
        return {
            "action_type": action_type,
            "action_hash": hashlib.sha256(action.model_dump_json().encode()).hexdigest(),
            "agent_action_ref": ref,
            "tool_action_refs": [],
            "side_effect_receipt_refs": receipt_refs,
        }

    async def _record_model_turn(
        self,
        response: ModelResponse | None,
        context_update: ContextUpdate | None,
        tool_actions: list[ToolAction],
        iteration: int,
        context: RunContext,
    ) -> list[str]:
        """Persist the model turn's thread-memory side effects.

        Compaction responses are excluded: their outcome is applied by
        CompactionApplyRule, not by generic turn recording.
        """
        if response is None or (not response.content and not tool_actions):
            return []
        receipt_refs: list[str] = []
        metadata: dict[str, object] = {"run_id": context.run_id}
        if tool_actions:
            metadata["tool_calls"] = [
                call.model_dump(mode="json") for call in tool_actions
            ]
        event_operation_id = f"memory:assistant-event:{context.run_id}:{iteration}"
        event = await self.runtime.memory_manager.append_event(
            session_id=context.thread_id,
            role="assistant",
            content_parts=merge_content_parts(text=response.content),
            ctx=context.to_tool_context(),
            metadata=metadata,
            operation_id=event_operation_id,
        )
        receipt_refs.append(
            await put_side_effect_receipt(
                self.runtime.persistence.artifact_manager,
                operation_id=event_operation_id,
                effect_type="memory.append_event",
                result_ref=event.event_id,
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
            )
        )
        if context_update is not None:
            update_operation_id = f"memory:context-update:{context.run_id}:{iteration}"
            await self.runtime.memory_manager.apply_context_update(
                context.thread_id,
                context.to_tool_context(),
                operation_id=update_operation_id,
                **context_update.model_dump(
                    exclude_none=True, exclude={"compacted"}
                ),
            )
            receipt_refs.append(
                await put_side_effect_receipt(
                    self.runtime.persistence.artifact_manager,
                    operation_id=update_operation_id,
                    effect_type="memory.apply_context_update",
                    result_ref=event.event_id,
                    tenant_id=context.tenant_id,
                    scope_id=context.run_id,
                )
            )
        return receipt_refs

    async def _form_compaction(
        self,
        response: ModelResponse | None,
        input: ResultProcessRuleInput,
        context: RunContext,
    ) -> ResultProcessStagePatch:
        artifact_manager = self.runtime.persistence.artifact_manager
        envelope = await get_model(
            artifact_manager, input.context_pack_ref, ModelContextEnvelope
        )
        response, context_update, memory_candidates = await self._interpret_response(
            response, input, context
        )
        if (
            response is None
            or context_update is None
            or not context_update.compacted
            or memory_candidates is None
            or envelope is None
            or envelope.operation != "compaction"
        ):
            action = FailureAction(
                error=SafeErrorRecord(
                    reason_code="context.compaction_invalid",
                    message="model did not return a valid compacted context",
                ),
                source_stage="result_process",
            )
            ref = await put_model(
                artifact_manager,
                action,
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
                operation_id=f"compaction-failure:{context.run_id}:{input.iteration}",
            )
            return {
                "action_type": "failure",
                "action_hash": hashlib.sha256(
                    action.model_dump_json().encode()
                ).hexdigest(),
                "agent_action_ref": ref,
                "tool_action_refs": [],
            }
        result = CompactionResult(
            context_update=context_update,
            long_term_memory_candidates=memory_candidates,
        )
        ref = await put_model(
            artifact_manager,
            result,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"compaction-result:{context.run_id}:{input.iteration}",
        )
        return {
            "action_type": "compaction",
            "action_hash": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
            "agent_action_ref": ref,
            "tool_action_refs": [],
        }

    async def _interpret_response(
        self,
        response: ModelResponse | None,
        input: ResultProcessRuleInput,
        context: RunContext,
    ) -> tuple[
        ModelResponse | None,
        ContextUpdate | None,
        list[LongTermMemoryCandidate] | None,
    ]:
        """Parse structured model content only inside ResultProcess."""
        if response is None or not response.content:
            return response, None, None
        try:
            structured = json.loads(response.content)
        except json.JSONDecodeError:
            return response, None, None
        if not isinstance(structured, dict) or "answer" not in structured:
            return response, None, None
        content = str(structured["answer"])
        update_payload = structured.get("context_update")
        try:
            update = (
                ContextUpdate.model_validate(update_payload)
                if isinstance(update_payload, dict)
                else None
            )
        except (ValueError, TypeError):
            # A malformed optional context update must not poison a valid
            # final answer. Compaction separately requires a valid update and
            # will still fail closed in _form_compaction.
            update = None
        candidates_payload = structured.get("long_term_memory_candidates")
        candidates: list[LongTermMemoryCandidate] | None = None
        if isinstance(candidates_payload, list):
            try:
                candidates = [
                    LongTermMemoryCandidate.model_validate(candidate)
                    for candidate in candidates_payload
                ]
            except (ValueError, TypeError):
                candidates = None
        content_ref = await put_bytes(
            self.runtime.persistence.artifact_manager,
            content.encode(),
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"result-content:{context.run_id}:{input.iteration}",
        )
        return (
            response.model_copy(
                update={
                    "content": content,
                    "content_ref": ArtifactRef(
                        artifact_id=content_ref,
                        content_type="text/plain",
                    ),
                }
            ),
            update,
            candidates,
        )

    @staticmethod
    def _form_tool_actions(calls: list[ModelToolCall]) -> list[ToolAction]:
        """Parse raw provider arguments and form provider-independent actions."""
        actions: list[ToolAction] = []
        for call in calls:
            arguments: dict[str, object] = {}
            arguments_error: str | None = None
            text = call.raw_arguments.strip()
            if not text:
                arguments_error = "empty arguments"
            else:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    arguments_error = text[:200]
                else:
                    if isinstance(parsed, dict):
                        arguments = parsed
                    else:
                        arguments_error = (
                            "arguments must be a JSON object, got "
                            f"{type(parsed).__name__}"
                        )
            actions.append(
                ToolAction(
                    tool_name=call.tool_name,
                    tool_call_id=call.tool_call_id,
                    arguments=arguments,
                    arguments_error=arguments_error,
                )
            )
        return actions
