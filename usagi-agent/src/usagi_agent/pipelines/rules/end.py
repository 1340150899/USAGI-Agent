from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.rules.stage import EndRuleInput, EndRuleOutput, RuleExecutionError
if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class EndAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.END]

    @abstractmethod
    async def end(
        self,
        input: EndRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> EndRuleOutput | RuleExecutionError | None:
        raise NotImplementedError
