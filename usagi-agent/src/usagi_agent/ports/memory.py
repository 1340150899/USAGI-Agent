"""Memory Port (design §22.8)."""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.policy import MemoryCandidate
from usagi_agent.types.refs import MemoryRef
from usagi_agent.memory.types import (
    ContextPolicy, LongTermMemory, MemoryExtractionRequest, PreparedContext,
    RawEvent, SessionContext,
)


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
    async def append_event(
        self, *, session_id: str, role: str, content: str, ctx: ToolContext,
        metadata: dict[str, object] | None = None,
    ) -> RawEvent: ...

    async def get_session_context(
        self, session_id: str, ctx: ToolContext,
    ) -> SessionContext: ...

    async def prepare_context(
        self, session_id: str, ctx: ToolContext, policy: ContextPolicy,
    ) -> PreparedContext: ...

    async def apply_context_update(
        self, session_id: str, ctx: ToolContext, **updates: object,
    ) -> SessionContext: ...

    async def apply_compaction(
        self, session_id: str, event_ids: list[str], ctx: ToolContext,
        **updates: object,
    ) -> SessionContext: ...

    async def recall(self, query: RecallQuery, ctx: ToolContext) -> MemoryRecallResult: ...

    async def extract(
        self, request: MemoryExtractionRequest, ctx: ToolContext,
    ) -> list[LongTermMemory]: ...

    async def put(self, memory: LongTermMemory, ctx: ToolContext) -> LongTermMemory: ...

    async def propose(
        self, candidate: MemoryCandidate, ctx: ToolContext,
    ) -> MemoryMutationResult: ...

    async def revoke(self, memory_id: str, ctx: ToolContext) -> MemoryMutationResult: ...

    async def health(self) -> HealthStatus: ...
