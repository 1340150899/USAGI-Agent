from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.rules.stage import (
    PreRecallRuleInput,
    PreRecallRuleOutput,
    RuleExecutionError,
)

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime

class PreRecallAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.PRE_RECALL]

    @abstractmethod
    async def pre_recall(
        self,
        input: PreRecallRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> PreRecallRuleOutput | RuleExecutionError | None:
        raise NotImplementedError
