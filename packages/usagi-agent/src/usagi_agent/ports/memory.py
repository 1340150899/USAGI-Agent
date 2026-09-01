"""Memory Port (design §22.8)."""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.policy import MemoryCandidate
from usagi_agent.types.refs import MemoryRef


class MemoryRecallResult(BaseModel):
    hits: list = Field(default_factory=list)  # list[MemoryHit]
    reason_codes: list[str] = Field(default_factory=list)


class MemoryMutationResult(BaseModel):
    status: Literal["applied", "already_applied", "conflict", "failed", "unknown"]
    memory_ref: MemoryRef | None = None
    resulting_version: int | None = None
    reason_codes: list[str] = Field(default_factory=list)


@runtime_checkable
class MemoryManager(Protocol):
    async def recall(self, query: RecallQuery, ctx: ToolContext) -> MemoryRecallResult: ...

    async def propose(
        self, candidate: MemoryCandidate, ctx: ToolContext,
    ) -> MemoryMutationResult: ...

    async def revoke(self, memory_id: str, ctx: ToolContext) -> MemoryMutationResult: ...

    async def health(self) -> HealthStatus: ...
