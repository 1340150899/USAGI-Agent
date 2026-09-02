from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.rules.stage import ModelRuleInput, ModelRuleOutput, RuleExecutionError
if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class ModelRuleAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.MODEL]

    @abstractmethod
    async def model(
        self,
        input: ModelRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> ModelRuleOutput | RuleExecutionError | None:
        raise NotImplementedError
