from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_model, put_bytes, put_model
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import ModelRuleAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ModelRuleInput,
    ModelRuleOutput,
    RuleExecutionError,
    StatePatch,
)
from usagi_agent.types.model import ModelRequest
from usagi_agent.types.refs import ArtifactRef

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class ModelProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[ModelRuleAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules

    async def process(self, state: AgentRunState, context: RunContext) -> StatePatch:
        rule_input = ModelRuleInput(
            context_pack_ref=state.get("context_pack_ref", ""),
            model_request_ref=state.get("model_request_ref", ""),
            iteration=state.get("iteration", 0),
            model_response_ref=state.get("model_response_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.rules:
            result = await config.model(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, ModelRuleOutput):
                raise TypeError("model rule returned an invalid output")
            stage_patch["model_response_ref"] = result.model_response_ref
            rule_input = rule_input.model_copy(
                update={"model_response_ref": result.model_response_ref}
            )
        stage_patch["model_response_ref"] = await self._invoke_model(
            rule_input, context
        )
        return stage_patch

    async def _invoke_model(self, input: ModelRuleInput, context: RunContext) -> str:
        request = await get_model(
            self.runtime.persistence.artifact_manager,
            input.model_request_ref,
            ModelRequest,
        )
        if request is None:
            raise RuntimeError("missing prepared model request artifact")
        response = await self.runtime.agent_manager.generate(
            agent_id=self.agent.id,
            request=request,
            context=context,
        )
        if response.content is not None:
            content_ref = await put_bytes(
                self.runtime.persistence.artifact_manager,
                response.content.encode(),
                tenant_id=context.tenant_id,
                scope_id=context.run_id,
                operation_id=f"model-content:{context.run_id}:{input.iteration}",
            )
            response = response.model_copy(
                update={
                    "content_ref": ArtifactRef(
                        artifact_id=content_ref, content_type="text/plain"
                    )
                }
            )
        return await put_model(
            self.runtime.persistence.artifact_manager,
            response,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"model:{context.run_id}:{input.iteration}",
        )
