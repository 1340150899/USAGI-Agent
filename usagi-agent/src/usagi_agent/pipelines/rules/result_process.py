from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.stage import (
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
    RuleExecutionError,
)
if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class ResultProcessAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.RESULT_PROCESS]

    @abstractmethod
    async def process_result(
        self,
        input: ResultProcessRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> ResultProcessRuleOutput | RuleExecutionError | None:
        raise NotImplementedError
