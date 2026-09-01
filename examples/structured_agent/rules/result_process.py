import hashlib

from usagi_agent.kernel import RunContext
from usagi_agent.pipelines.artifacts import get_model, put_model
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules import ResultProcessAdapterConfig, StatePatch
from usagi_agent.types.action import FailureAction, FinalAction, SafeErrorRecord
from usagi_agent.types.model import ModelResponse


class ResearchWriterResultProcessConfig(ResultProcessAdapterConfig):
    async def process_result(self, state: AgentRunState, runtime, context: RunContext) -> StatePatch:
        response = await get_model(
            runtime.persistence.artifact_manager, state.get("model_response_ref", ""), ModelResponse
        )
        if response is None:
            action = FailureAction(
                error=SafeErrorRecord(reason_code="model.no_response", message="missing"),
                source_stage="result_process",
            )
            action_type = "failure"
        elif response.tool_calls:
            action = response.tool_calls[0]
            action_type = "tool"
        else:
            action = FinalAction(
                output_ref=response.content_ref, output_schema="usagi.final_output@1.0.0"
            )
            action_type = "final"
        ref = await put_model(
            runtime.persistence.artifact_manager,
            action,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"action:{context.run_id}:{state.get('iteration', 0)}",
        )
        return {
            "action_type": action_type,
            "action_hash": hashlib.sha256(action.model_dump_json().encode()).hexdigest(),
            "agent_action_ref": ref,
        }
