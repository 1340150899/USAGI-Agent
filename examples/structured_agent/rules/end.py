from datetime import datetime, timezone

from langgraph.types import interrupt

from usagi_agent.kernel import RunContext
from usagi_agent.persistence.ports.approval import ApprovalTask
from usagi_agent.pipelines.artifacts import get_model, put_model
from usagi_agent.pipelines.rules import EndAdapterConfig, EndRuleInput, EndRuleOutput
from usagi_agent.types.action import FinalAction, ToolAction


class ResearchWriterEndConfig(EndAdapterConfig):
    async def end(
        self, input: EndRuleInput, runtime, context: RunContext
    ) -> EndRuleOutput:
        action_type = input.action_type
        iteration = input.iteration
        if action_type == "tool":
            action = await get_model(
                runtime.persistence.artifact_manager,
                input.agent_action_ref,
                ToolAction,
            )
            if action is None:
                return EndRuleOutput(
                    pass_disposition="run_failed", iteration=iteration + 1
                )
            decision = await runtime.policy_engine.evaluate(
                principal=context.principal,
                action="tool.execute",
                tool_name=action.tool_name,
                arguments=action.arguments,
                context=context.to_tool_context(),
            )
            if decision.effect == "deny":
                return EndRuleOutput(
                    pass_disposition="run_failed", iteration=iteration + 1
                )
            if decision.effect == "require_approval":
                approval_id = f"approval_{context.run_id}_{action.tool_call_id}"
                approval = await runtime.persistence.approval_store.get_or_create(
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
                if not isinstance(resumed, dict):
                    return EndRuleOutput(
                        pass_disposition="run_failed", iteration=iteration + 1
                    )
                if (
                    resumed.get("kind") != "approval"
                    or resumed.get("approval_id") != approval.approval_id
                    or resumed.get("action_hash") != approval.action_hash
                    or tuple(resumed.get("approval_scope", ())) != approval.approval_scope
                ):
                    return EndRuleOutput(
                        pass_disposition="run_failed", iteration=iteration + 1
                    )
                decided = await runtime.persistence.approval_store.cas_decide(
                    approval.approval_id,
                    expected_version=int(resumed.get("expected_approval_version", -1)),
                    decision=resumed.get("decision", "reject"),
                    evidence_ref=None,
                )
                if decided.status != "approved":
                    return EndRuleOutput(
                        pass_disposition="run_failed", iteration=iteration + 1
                    )
            observation = await runtime.tool_manager.execute(
                name=action.tool_name,
                arguments=action.arguments,
                context=context.to_tool_context(),
                tool_call_id=action.tool_call_id,
            )
            ref = await put_model(
                runtime.persistence.artifact_manager,
                observation,
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
                operation_id=f"tool:{context.run_id}:{action.tool_call_id}",
            )
            return EndRuleOutput(
                tool_observation_refs=(ref,),
                pass_disposition=(
                    "next_pass" if observation.status == "success" else "run_failed"
                ),
                iteration=iteration + 1,
            )
        if action_type == "final":
            action = await get_model(
                runtime.persistence.artifact_manager,
                input.agent_action_ref,
                FinalAction,
            )
            return EndRuleOutput(
                final_output_ref=action.output_ref.artifact_id if action else "",
                pass_disposition="run_completed",
                iteration=iteration + 1,
            )
        return EndRuleOutput(
            pass_disposition="run_failed",
            iteration=iteration + 1,
        )
