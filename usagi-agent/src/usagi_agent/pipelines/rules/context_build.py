"""Rule contracts for filtering and ranking recalled context."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.rules.stage import RuleExecutionError
from usagi_agent.pipelines.rules.stage_type import StageType

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class ContextFilterAdapterConfig(BaseModel, ABC):
    """Return ``True`` when one recalled context item should be discarded."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: Literal[StageType.CONTEXT_BUILD] = StageType.CONTEXT_BUILD

    @abstractmethod
    async def filter_context(
        self,
        recalled_context: object,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> bool | RuleExecutionError:
        raise NotImplementedError


class ContextRankAdapterConfig(BaseModel, ABC):
    """Sort recalled context items in place; return ``None`` on success."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: Literal[StageType.CONTEXT_BUILD] = StageType.CONTEXT_BUILD

    @abstractmethod
    async def rank_context(
        self,
        recalled_contexts: list[object],
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> RuleExecutionError | None:
        raise NotImplementedError
