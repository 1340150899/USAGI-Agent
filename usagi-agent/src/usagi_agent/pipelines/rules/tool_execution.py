"""Framework rule: govern and execute formed ToolActions (design §20.4 chain).

Runs inside the ResultProcess stage. For every formed tool action:
parse-error feedback -> policy -> approval (interrupt) ->
ToolRuntime.execute -> persist observation -> append the model-facing tool
event -> persist a reusable observation candidate. Calls execute serially;
the switch to bounded parallelism stays inside this rule and does not change
the rule contract. Every failure becomes a reason-carrying observation fed
back to the model. Rejecting an approval denies only that action; remaining
actions in the same model response continue through their own approval gates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from langgraph.types import interrupt

from usagi_agent.kernel.context import RunContext
from usagi_agent.memory.types import ToolObservationMemory
from usagi_agent.observability import operation
from usagi_agent.pipelines.artifacts import (
    get_model,
    put_model,
    put_side_effect_receipt,
)
from usagi_agent.pipelines.rules.result_process import ResultProcessAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
    RuleExecutionError,
)
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.tools.render import render_model_content
from usagi_agent.types.action import ToolAction, ToolObservation
from usagi_agent.types.content import merge_content_parts

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


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
            with operation(
                runtime.observability,
                "tool.execute",
                **{"usagi.tool.name": action.tool_name},
            ) as telemetry:
                outcome = await self._execute_one(
                    action, runtime, context, operation_id=tool_operation_id
                )
                telemetry.set_outcome(outcome.observation.status)
                if outcome.observation.error_code:
                    telemetry.set_attribute(
                        "error.type", outcome.observation.error_code
                    )
                runtime.observability.record_tool(outcome.observation)
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
                session_id=context.memory_session_id,
                role="tool",
                content_parts=merge_content_parts(
                    text=render_model_content(observation),
                    parts=observation.content_parts,
                ),
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
                    f"memory:tool-observation:{context.run_id}:{action.tool_call_id}"
                )
                candidate = await self._persist_observation_candidate(
                    action,
                    observation,
                    outcome.arguments,
                    event.event_id,
                    runtime,
                    context,
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
    ) -> _ExecutionOutcome:
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
                        f"model arguments were not valid JSON: {action.arguments_error}"
                    ),
                ),
                arguments=arguments,
            )
        with operation(
            runtime.observability,
            "policy.evaluate",
            **{
                "usagi.policy.action": "tool.execute",
                "usagi.tool.name": action.tool_name,
            },
        ) as telemetry:
            decision = await runtime.policy_engine.evaluate(
                principal=context.principal,
                action="tool.execute",
                tool_name=action.tool_name,
                arguments=arguments,
                context=tool_context,
            )
            telemetry.set_outcome(decision.effect)
            runtime.observability.record_policy(
                action="tool.execute", effect=decision.effect
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
        from usagi_agent.tools.approval import ToolApprovalRequired

        async def execute(approval_result=None):
            return await runtime.tool_manager.execute(
                name=action.tool_name,
                arguments=arguments,
                context=tool_context,
                tool_call_id=action.tool_call_id,
                operation_id=operation_id,
                approval_result=approval_result,
            )

        try:
            observation = await execute()
        except ToolApprovalRequired as pending:
            resumed = interrupt(pending.payload())
            if not await runtime.tool_manager.approvals.decide(pending, resumed):
                return _ExecutionOutcome(
                    observation=ToolObservation(
                        tool_name=action.tool_name,
                        tool_call_id=action.tool_call_id,
                        status="denied",
                        error_code="tool.approval_rejected",
                        error_message="The user rejected this tool execution request.",
                    ),
                    arguments=arguments,
                )
            observation = await execute(resumed)
        return _ExecutionOutcome(observation=observation, arguments=arguments)

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
