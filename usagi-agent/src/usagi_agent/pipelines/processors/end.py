from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from langgraph.types import interrupt

from usagi_agent.kernel.context import RunContext
from usagi_agent.persistence.ports.approval import ApprovalTask
from usagi_agent.pipelines.artifacts import get_model, put_model
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import EndAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    EndRuleInput,
    EndRuleOutput,
    RuleExecutionError,
    StatePatch,
)
from usagi_agent.types.action import FinalAction, ToolAction

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class EndProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[EndAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules

    async def process(self, state: AgentRunState, context: RunContext) -> StatePatch:
        rule_input = EndRuleInput(
            action_type=state.get("action_type", ""),
            action_hash=state.get("action_hash", ""),
            agent_action_ref=state.get("agent_action_ref", ""),
            iteration=state.get("iteration", 0),
            pass_disposition=state.get("pass_disposition", ""),
            tool_observation_refs=tuple(state.get("tool_observation_refs", [])),
            final_output_ref=state.get("final_output_ref", ""),
        )
        stage_patch: StatePatch = {}
        new_observation_refs: list[str] = []
        for config in self.rules:
            result = await config.end(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, EndRuleOutput):
                raise TypeError("end rule returned an invalid output")
            stage_patch["pass_disposition"] = result.pass_disposition
            stage_patch["iteration"] = result.iteration
            if result.final_output_ref is not None:
                stage_patch["final_output_ref"] = result.final_output_ref
            for ref in result.tool_observation_refs:
                if ref not in new_observation_refs:
                    new_observation_refs.append(ref)
            if new_observation_refs:
                stage_patch["tool_observation_refs"] = new_observation_refs
            rule_input = rule_input.model_copy(
                update={
                    "pass_disposition": result.pass_disposition,
                    "iteration": result.iteration,
                    "tool_observation_refs": tuple(
                        dict.fromkeys(
                            (*rule_input.tool_observation_refs, *result.tool_observation_refs)
                        )
                    ),
                    "final_output_ref": (
                        result.final_output_ref
                        if result.final_output_ref is not None
                        else rule_input.final_output_ref
                    ),
                }
            )
        if "pass_disposition" not in stage_patch:
            result = await self._default_end(rule_input, context)
            stage_patch.update(result.model_dump(exclude_none=True))
        return stage_patch

    async def _default_end(self, input: EndRuleInput, context: RunContext) -> EndRuleOutput:
        iteration = input.iteration + 1
        if input.action_type == "compaction":
            return EndRuleOutput(
                pass_disposition="next_pass",
                iteration=iteration,
            )
        if input.action_type == "tool":
            action = await get_model(
                self.runtime.persistence.artifact_manager,
                input.agent_action_ref,
                ToolAction,
            )
            if action is None:
                return EndRuleOutput(pass_disposition="run_failed", iteration=iteration)
            decision = await self.runtime.policy_engine.evaluate(
                principal=context.principal,
                action="tool.execute",
                tool_name=action.tool_name,
                arguments=action.arguments,
                context=context.to_tool_context(),
            )
            if decision.effect == "deny":
                return EndRuleOutput(pass_disposition="run_failed", iteration=iteration)
            if decision.effect == "require_approval":
                approval_id = f"approval_{context.run_id}_{action.tool_call_id}"
                approval = await self.runtime.persistence.approval_store.get_or_create(
                    ApprovalTask(
                        approval_id=approval_id,
                        run_id=context.run_id,
                        approval_operation_id=approval_id,
                        interrupt_id=approval_id,
                        action_hash=input.action_hash,
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
                    return EndRuleOutput(
                        pass_disposition="run_failed", iteration=iteration
                    )
                decided = await self.runtime.persistence.approval_store.cas_decide(
                    approval.approval_id,
                    expected_version=int(
                        resumed.get("expected_approval_version", -1)
                    ),
                    decision=resumed.get("decision", "reject"),
                    evidence_ref=None,
                )
                if decided.status != "approved":
                    return EndRuleOutput(
                        pass_disposition="run_failed", iteration=iteration
                    )
            observation = await self.runtime.tool_manager.execute(
                name=action.tool_name,
                arguments=action.arguments,
                context=context.to_tool_context(),
                tool_call_id=action.tool_call_id,
            )
            ref = await put_model(
                self.runtime.persistence.artifact_manager,
                observation,
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
                operation_id=f"tool:{context.run_id}:{action.tool_call_id}",
            )
            await self.runtime.memory_manager.append_event(
                session_id=context.thread_id,
                role="tool",
                content=observation.model_dump_json(),
                ctx=context.to_tool_context(),
                metadata={
                    "tool_name": action.tool_name,
                    "tool_call_id": action.tool_call_id,
                },
            )
            return EndRuleOutput(
                pass_disposition=(
                    "next_pass" if observation.status == "success" else "run_failed"
                ),
                iteration=iteration,
                tool_observation_refs=(ref,),
            )
        if input.action_type == "final":
            action = await get_model(
                self.runtime.persistence.artifact_manager,
                input.agent_action_ref,
                FinalAction,
            )
            return EndRuleOutput(
                pass_disposition="run_completed",
                iteration=iteration,
                final_output_ref=action.output_ref.artifact_id if action else "",
            )
        return EndRuleOutput(pass_disposition="run_failed", iteration=iteration)
