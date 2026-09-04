"""Framework rule that executes one fully prepared model request."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_model, put_bytes, put_model
from usagi_agent.pipelines.rules.model import ModelRuleAdapterConfig
from usagi_agent.pipelines.rules.stage import ModelRuleInput, ModelRuleOutput
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.types.model import ModelRequest
from usagi_agent.types.refs import ArtifactRef

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class ModelExecutionRule(ModelRuleAdapterConfig):
    """Call AgentManager and return the persisted response reference."""

    name: str = "model_execution"
    type: Literal[StageType.MODEL] = StageType.MODEL

    async def model(
        self,
        input: ModelRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> ModelRuleOutput:
        request = await get_model(
            runtime.persistence.artifact_manager,
            input.model_request_ref,
            ModelRequest,
        )
        if request is None:
            raise RuntimeError("missing prepared model request artifact")
        response = await runtime.agent_manager.generate(
            agent_id=input.agent_id,
            request=request,
            context=context.to_tool_context(),
        )
        if response.content is not None:
            content_ref = await put_bytes(
                runtime.persistence.artifact_manager,
                response.content.encode(),
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
                operation_id=f"model-content:{context.run_id}:{input.iteration}",
            )
            response = response.model_copy(
                update={
                    "content_ref": ArtifactRef(
                        artifact_id=content_ref,
                        content_type="text/plain",
                    )
                }
            )
        response_ref = await put_model(
            runtime.persistence.artifact_manager,
            response,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"model:{context.run_id}:{input.iteration}",
        )
        return ModelRuleOutput(model_response_ref=response_ref)
