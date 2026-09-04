"""Framework rule: govern and execute formed ToolActions (design §20.4 chain).

Runs inside the ResultProcess stage. For every formed tool action:
parse-error feedback -> policy -> approval (interrupt) ->
ToolRuntime.execute -> persist observation -> append the model-facing tool
event -> persist a reusable observation candidate. Calls execute serially;
the switch to bounded parallelism stays inside this rule and does not change
the rule contract. Every failure becomes a reason-carrying observation fed
back to the model — only a rejected approval or an ``unknown`` outcome
terminates the pass.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from langgraph.types import interrupt

from usagi_agent.kernel.context import RunContext
from usagi_agent.memory.types import ToolObservationMemory
from usagi_agent.pipelines.artifacts import (
    get_model,
    put_model,
    put_side_effect_receipt,
)
from usagi_agent.persistence.ports.approval import ApprovalTask
from usagi_agent.pipelines.rules.result_process import ResultProcessAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
    RuleExecutionError,
)
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.tools.render import render_model_content
from usagi_agent.types.action import ToolAction, ToolObservation

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class _Rejected:
    """Sentinel: a human rejected approval; the pass terminates."""


_REJECTED = _Rejected()


@dataclass
class _ExecutionOutcome:
    observation: ToolObservation
    arguments: dict[str, object]


class ToolExecutionRule(ResultProcessAdapterConfig):
    name: str = "tool_execution"
    type: Literal[StageType.RESULT_PROCESS] = StageType.RESULT_PROCESS

    async def process_result(
        self,
        input: ResultProcessRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> ResultProcessRuleOutput | RuleExecutionError | None:
        if input.action_type != "tool":
            return None
        artifact_manager = runtime.persistence.artifact_manager
        observation_refs: list[str] = []
        receipt_refs: list[str] = []
        for action_ref in input.tool_action_refs:
            action = await get_model(artifact_manager, action_ref, ToolAction)
            if action is None:
                return RuleExecutionError(
                    reason_code="tool.action_missing",
                    message=f"tool action artifact not found: {action_ref}",
                )
            tool_operation_id = f"tool:execute:{context.run_id}:{action.tool_call_id}"
            outcome = await self._execute_one(
                action, runtime, context, operation_id=tool_operation_id
            )
            if isinstance(outcome, _Rejected):
                return ResultProcessRuleOutput(
                    pass_disposition="run_failed",
                    tool_observation_refs=tuple(observation_refs),
                    reason_codes=("tool.approval_rejected",),
                    side_effect_receipt_refs=tuple(receipt_refs),
                )
            observation = outcome.observation
            ref = await put_model(
                artifact_manager,
                observation,
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
                operation_id=f"tool:{context.run_id}:{action.tool_call_id}",
            )
            await runtime.tool_manager.attach_observation(tool_operation_id, ref)
            receipt_refs.append(
                await put_side_effect_receipt(
                    artifact_manager,
                    operation_id=tool_operation_id,
                    effect_type="tool.execute",
                    result_ref=ref,
                    tenant_id=context.tenant_id,
                    scope_id=context.run_id,
                )
            )
            event_operation_id = (
                f"memory:tool-event:{context.run_id}:{action.tool_call_id}"
            )
            event = await runtime.memory_manager.append_event(
                session_id=context.thread_id,
                role="tool",
                content=render_model_content(observation),
                ctx=context.to_tool_context(),
                metadata={
                    "tool_name": action.tool_name,
                    "tool_call_id": action.tool_call_id,
                },
                operation_id=event_operation_id,
            )
            receipt_refs.append(
                await put_side_effect_receipt(
                    artifact_manager,
                    operation_id=event_operation_id,
                    effect_type="memory.append_event",
                    result_ref=event.event_id,
                    tenant_id=context.tenant_id,
                    scope_id=context.run_id,
                )
            )
            if observation.status == "success":
                candidate_operation_id = (
                    f"memory:tool-observation:{context.run_id}:"
                    f"{action.tool_call_id}"
                )
                candidate = await self._persist_observation_candidate(
                    action, observation, outcome.arguments,
                    event.event_id, runtime, context,
                    operation_id=candidate_operation_id,
                )
                receipt_refs.append(
                    await put_side_effect_receipt(
                        artifact_manager,
                        operation_id=candidate_operation_id,
                        effect_type="memory.put_tool_observation",
                        result_ref=candidate.memory_id,
                        tenant_id=context.tenant_id,
                        scope_id=context.run_id,
                    )
                )
            observation_refs.append(ref)
        return ResultProcessRuleOutput(
            tool_observation_refs=tuple(observation_refs),
            side_effect_receipt_refs=tuple(receipt_refs),
        )

    async def _execute_one(
        self,
        action: ToolAction,
        runtime: "ServerRuntime",
        context: RunContext,
        *,
        operation_id: str,
    ) -> _ExecutionOutcome | _Rejected:
        tool_context = context.to_tool_context()
        arguments: dict[str, object] = dict(action.arguments)
        if action.arguments_error is not None:
            return _ExecutionOutcome(
                observation=ToolObservation(
                    tool_name=action.tool_name,
                    tool_call_id=action.tool_call_id,
                    status="failed",
                    error_code="tool.parse_error",
                    error_message=(
                        "model arguments were not valid JSON: "
                        f"{action.arguments_error}"
                    ),
                ),
                arguments=arguments,
            )
        decision = await runtime.policy_engine.evaluate(
            principal=context.principal,
            action="tool.execute",
            tool_name=action.tool_name,
            arguments=arguments,
            context=tool_context,
        )
        if decision.effect == "deny":
            reasons = ", ".join(decision.reason_codes) or "policy denied"
            return _ExecutionOutcome(
                observation=ToolObservation(
                    tool_name=action.tool_name,
                    tool_call_id=action.tool_call_id,
                    status="denied",
                    error_code="tool.denied",
                    error_message=f"policy denied execution: {reasons}",
                ),
                arguments=arguments,
            )
        if decision.effect == "require_approval":
            approval_id = f"approval_{context.run_id}_{action.tool_call_id}"
            approval = await runtime.persistence.approval_store.get_or_create(
                ApprovalTask(
                    approval_id=approval_id,
                    run_id=context.run_id,
                    approval_operation_id=approval_id,
                    interrupt_id=approval_id,
                    action_hash=hashlib.sha256(
                        action.model_dump_json().encode()
                    ).hexdigest(),
                    approval_scope=("tool.execute", action.tool_name),
                    tool_name=action.tool_name,
                    status="pending",
                    version=0,
                    created_at=datetime.now(timezone.utc),
                )
            )
            resumed = interrupt(
                {
                    "kind": "approval",
                    "approval_id": approval.approval_id,
                    "approval_version": approval.version,
                    "approval_scope": approval.approval_scope,
                    "action_hash": approval.action_hash,
                    "tool_name": action.tool_name,
                }
            )
            if not isinstance(resumed, dict) or (
                resumed.get("kind") != "approval"
                or resumed.get("approval_id") != approval.approval_id
                or resumed.get("action_hash") != approval.action_hash
                or tuple(resumed.get("approval_scope", ()))
                != approval.approval_scope
            ):
                return _REJECTED
            decided = await runtime.persistence.approval_store.cas_decide(
                approval.approval_id,
                expected_version=int(
                    resumed.get("expected_approval_version", -1)
                ),
                decision=resumed.get("decision", "reject"),
                evidence_ref=None,
            )
            if decided.status != "approved":
                return _REJECTED
        return _ExecutionOutcome(
            observation=await runtime.tool_manager.execute(
                name=action.tool_name,
                arguments=arguments,
                context=tool_context,
                tool_call_id=action.tool_call_id,
                operation_id=operation_id,
            ),
            arguments=arguments,
        )

    @staticmethod
    async def _persist_observation_candidate(
        action: ToolAction,
        observation: ToolObservation,
        arguments: dict[str, object],
        event_id: str,
        runtime: "ServerRuntime",
        context: RunContext,
        *,
        operation_id: str,
    ) -> ToolObservationMemory:
        """Persist a successful observation as a reusable candidate.

        Key is (tool, normalized arguments) so put_tool_observation's
        supersede keeps exactly one active record per call shape — recall
        always returns the freshest result. Failures are not persisted: they
        already fed back in-thread and would only pollute recall.
        """
        arguments_digest = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        return await runtime.memory_manager.put_tool_observation(
            ToolObservationMemory(
                memory_id=f"memory_{uuid4().hex}",
                namespace="",  # MemoryManager fills the namespace
                key=f"tool:{action.tool_name}:{arguments_digest}",
                tool_name=action.tool_name,
                arguments_digest=arguments_digest,
                content=render_model_content(observation),
                source_event_ids=[event_id],
            ),
            context.to_tool_context(),
            operation_id=operation_id,
        )
