from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import ModelRuleAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ModelRuleInput,
    ModelRuleOutput,
    RuleExecutionError,
    ModelStagePatch,
)

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

    async def process(self, state: AgentRunState, context: RunContext) -> ModelStagePatch:
        rule_input = ModelRuleInput(
            agent_id=self.agent.id,
            context_pack_ref=state.get("context_pack_ref", ""),
            model_request_ref=state.get("model_request_ref", ""),
            iteration=state.get("iteration", 0),
            model_response_ref=state.get("model_response_ref", ""),
        )
        stage_patch: ModelStagePatch = {}
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
        if not stage_patch.get("model_response_ref"):
            raise RuntimeError("model stage produced no model response")
        return stage_patch
