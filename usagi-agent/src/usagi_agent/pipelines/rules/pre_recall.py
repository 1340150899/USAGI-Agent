from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules.stage_type import StageType

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime

StatePatch = dict[str, object]


class PreRecallAdapterConfig(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)
    name: str
    type: Literal[StageType.PRE_RECALL]

    @abstractmethod
    async def pre_recall(self, state: AgentRunState, runtime: "ServerRuntime", context: RunContext) -> StatePatch:
        raise NotImplementedError
