"""Memory Port (design §22.8)."""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.ports.context import HealthStatus, ToolContext
from usagi_agent.types.context import LongTermMemoryCandidate, RecallQuery
from usagi_agent.types.policy import MemoryCandidate
from usagi_agent.types.refs import MemoryRef
from usagi_agent.types.content import ContentPart
from usagi_agent.memory.types import (
    ContextPolicy, LongTermMemory, MemoryExtractionRequest, PreparedContext,
    RawEvent, SessionContext, ToolObservationMemory,
)


class MemoryMutationResult(BaseModel):
    status: Literal["applied", "already_applied", "conflict", "failed", "unknown"]
    memory_ref: MemoryRef | None = None
    resulting_version: int | None = None
    reason_codes: list[str] = Field(default_factory=list)


@runtime_checkable
class MemoryManager(Protocol):
    async def append_event(
        self, *, session_id: str,
        role: Literal["user", "assistant", "tool", "system"],
        content_parts: list[ContentPart], ctx: ToolContext,
        metadata: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> RawEvent: ...

    async def list_events(
        self, session_id: str, ctx: ToolContext,
    ) -> list[RawEvent]: ...

    async def get_session_context(
        self, session_id: str, ctx: ToolContext,
    ) -> SessionContext: ...

    async def prepare_context(
        self, session_id: str, ctx: ToolContext, policy: ContextPolicy,
    ) -> PreparedContext: ...

    async def apply_context_update(
        self, session_id: str, ctx: ToolContext, *,
        operation_id: str | None = None, **updates: object,
    ) -> SessionContext: ...

    async def apply_compaction(
        self, session_id: str, event_ids: list[str], ctx: ToolContext,
        *, operation_id: str | None = None,
        memory_candidates: list[LongTermMemoryCandidate] | None = None,
        **updates: object,
    ) -> SessionContext: ...

    async def get_long_term_memories(
        self, query: RecallQuery, ctx: ToolContext,
    ) -> list[LongTermMemory]: ...

    async def put_tool_observation(
        self, record: ToolObservationMemory, ctx: ToolContext, *,
        operation_id: str | None = None,
    ) -> ToolObservationMemory: ...

    async def get_tool_observations(
        self, query: RecallQuery, ctx: ToolContext, *, limit: int = 10,
    ) -> list[ToolObservationMemory]: ...

    async def extract(
        self, request: MemoryExtractionRequest, ctx: ToolContext,
    ) -> list[LongTermMemory]: ...

    async def put(self, memory: LongTermMemory, ctx: ToolContext) -> LongTermMemory: ...

    async def propose(
        self, candidate: MemoryCandidate, ctx: ToolContext,
    ) -> MemoryMutationResult: ...

    async def revoke(self, memory_id: str, ctx: ToolContext) -> MemoryMutationResult: ...

    async def health(self) -> HealthStatus: ...
