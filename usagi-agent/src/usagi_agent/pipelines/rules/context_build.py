from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.rules.pre_recall import StatePatch
if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class ContextBuildAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.CONTEXT_BUILD]

    @abstractmethod
    async def build_context(self, state: AgentRunState, runtime: "ServerRuntime", context: RunContext) -> StatePatch:
        raise NotImplementedError
