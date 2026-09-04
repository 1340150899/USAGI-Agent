"""Short-term context and long-term memory contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryExtractionTrigger(StrEnum):
    EXPLICIT = "explicit"
    CONTEXT_COMPACTION = "context_compaction"


class RawEvent(BaseModel):
    event_id: str
    session_id: str
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionContext(BaseModel):
    """The model-visible working set; raw events remain stored separately."""

    summary: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    open_tasks: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    recent_event_ids: list[str] = Field(default_factory=list)
    compacted_until: str | None = None
    extracted_until: str | None = None
    applied_operation_ids: list[str] = Field(default_factory=list)


class ContextPolicy(BaseModel):
    context_window: int = Field(default=128_000, ge=1)
    compression_threshold: float = Field(default=0.8, gt=0, le=1)
    reserved_output_tokens: int = Field(default=4_096, ge=0)
    recent_message_tokens: int = Field(default=8_000, ge=1)
    force_compaction: bool = False


class PreparedContext(BaseModel):
    session: SessionContext
    recent_events: list[RawEvent] = Field(default_factory=list)
    events_to_compact: list[RawEvent] = Field(default_factory=list)
    estimated_tokens: int = 0
    compacted: bool = False

    @property
    def requires_compaction(self) -> bool:
        return bool(self.events_to_compact)


class MemoryExtractionRequest(BaseModel):
    session_id: str
    from_event_id: str | None = None
    to_event_id: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    trigger: MemoryExtractionTrigger = MemoryExtractionTrigger.EXPLICIT


class LongTermMemory(BaseModel):
    memory_id: str
    namespace: str
    key: str
    content: str
    type: Literal["semantic", "episodic", "procedural"] = "semantic"
    scope: Literal["user", "agent", "tenant"] = "user"
    confidence: float = 1.0
    importance: float = 0.5
    source_event_ids: list[str] = Field(default_factory=list)
    status: Literal["active", "superseded", "revoked"] = "active"
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: datetime | None = None


class ToolObservationMemory(BaseModel):
    """A recallable tool result, stored apart from distilled memories.

    Keyed by (tool, normalized-arguments digest) so repeated identical calls
    supersede to the freshest result. Lives in its own principal-scoped
    namespace — never mixed into LongTermMemory.
    """

    memory_id: str
    namespace: str
    key: str
    tool_name: str
    arguments_digest: str
    content: str
    status: Literal["active", "superseded"] = "active"
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_event_ids: list[str] = Field(default_factory=list)
