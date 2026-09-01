from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.stage import (
    ContextBuildRuleInput,
    ContextBuildRuleOutput,
    RuleExecutionError,
)
if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class ContextBuildAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.CONTEXT_BUILD]

    @abstractmethod
    async def build_context(
        self,
        input: ContextBuildRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> ContextBuildRuleOutput | RuleExecutionError | None:
        raise NotImplementedError
