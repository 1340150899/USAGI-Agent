"""Framework rule that executes one fully prepared model request."""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Literal

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_model, put_bytes, put_model
from usagi_agent.pipelines.rules.model import ModelRuleAdapterConfig
from usagi_agent.pipelines.rules.stage import ModelRuleInput, ModelRuleOutput
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.types.content import (
    ImageContentPart,
    parse_content_parts,
    select_content_parts,
)
from usagi_agent.types.model import ModelInputModality, ModelRequest
from usagi_agent.types.refs import ArtifactRef

if TYPE_CHECKING:
    from usagi_agent.persistence.ports.artifact import ArtifactManager
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
        model_spec = runtime.agent_manager.get(input.agent_id).model
        request = await self._prepare_model_input(
            request,
            model_spec.input_modalities,
            runtime.persistence.artifact_manager,
        )
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

    @classmethod
    async def _prepare_model_input(
        cls,
        request: ModelRequest,
        modalities: frozenset[ModelInputModality],
        artifact_manager: "ArtifactManager",
    ) -> ModelRequest:
        """Select supported parts and materialize binaries only for this call."""
        messages: list[dict[str, object]] = []
        for original in request.messages:
            message = dict(original)
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = content if "text" in modalities else ""
            elif isinstance(content, list):
                selected: list[object] = []
                parts = select_content_parts(
                    parse_content_parts(content), modalities
                )
                for part in parts:
                    if isinstance(part, ImageContentPart):
                        selected.append(
                            await cls._materialize_image(part, artifact_manager)
                        )
                    else:
                        selected.append(part.model_dump(mode="json"))
                message["content"] = selected
            messages.append(message)
        return request.model_copy(
            update={
                "system_prompt": (
                    request.system_prompt if "text" in modalities else ""
                ),
                "messages": messages,
            }
        )

    @staticmethod
    async def _materialize_image(
        image: ImageContentPart, artifact_manager: "ArtifactManager"
    ) -> dict[str, object]:
        if image.artifact_ref is None:
            return image.model_dump(mode="json")
        stream = await artifact_manager.get(image.artifact_ref, "run_execution")
        payload = b"".join([chunk async for chunk in stream])
        encoded = base64.b64encode(payload).decode("ascii")
        materialized = image.model_copy(
            update={
                "artifact_ref": None,
                "url": f"data:{image.media_type};base64,{encoded}",
            }
        )
        return materialized.model_dump(mode="json")
