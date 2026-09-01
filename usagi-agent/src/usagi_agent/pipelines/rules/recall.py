from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.stage import RecallRuleInput, RecallRuleOutput, RuleExecutionError
if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class RecallAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.RECALL]

    @abstractmethod
    async def recall(
        self,
        input: RecallRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> RecallRuleOutput | RuleExecutionError | None:
        raise NotImplementedError
