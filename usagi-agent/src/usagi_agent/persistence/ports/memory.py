"""Memory + Vector store Ports."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.refs import ArtifactRef, MemoryRef


class MemoryRecord(BaseModel):
    memory_id: str
    tenant_id: str
    namespace: str
    type: str
    content_ref: ArtifactRef
    embedding_ref: ArtifactRef | None = None
    scope_id: str
    confidence: float = 0.0
    version: int = 0
    status: str = "active"
    last_operation_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MemoryHit(BaseModel):
    memory_id: str
    content_ref: ArtifactRef
    score: float
    namespace: str


@runtime_checkable
class MemoryStore(Protocol):
    async def get(self, memory_id: str) -> MemoryRecord | None: ...

    async def list_by_namespace(self, tenant_id: str, namespace: str) -> list[MemoryRecord]: ...

    async def insert(self, record: MemoryRecord) -> MemoryRecord: ...

    async def update_status(self, memory_id: str, *, status: str, version: int) -> MemoryRecord: ...

    async def delete(self, memory_id: str) -> None: ...


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(self, memory_id: str, embedding_ref: ArtifactRef) -> None: ...

    async def search(self, embedding_ref: ArtifactRef, *, top_k: int) -> list[MemoryHit]: ...

    async def delete(self, memory_id: str) -> None: ...
