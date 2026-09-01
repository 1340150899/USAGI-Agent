"""Artifact data-plane Ports (design §24.4).

ArtifactRef is not a bearer credential. Every operation goes through ArtifactManager and
verifies tenant/subject/scope/tombstone/purpose. Metadata + lineage seed are reserved in
one DB transaction *before* the blob is uploaded; reliability comes from idempotent
operation IDs, the state machine, outbox and a sweeper (§24.4).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.refs import (
    ArtifactOwner,
    ArtifactRef,
    LineageParent,
    SecretRef,
    SettlementArtifactRef,
)
from usagi_agent.types.settlement import SettlementPermit

AsyncBytes = AsyncIterator[bytes]

ArtifactPurpose = Literal[
    "run_execution",
    "erasure_case",
    "memory_maintenance",
    "settlement",
    "audit",
]

ArtifactStatus = Literal[
    "absent",
    "reserved",
    "uploading",
    "uploaded",
    "available",
    "uploaded_quarantine",
    "settlement_quarantine",
    "adopted_available",
    "abandoned",
    "delete_pending",
    "deleted",
]


class ArtifactMetadata(BaseModel):
    artifact_id: str
    tenant_id: str
    owner: ArtifactOwner
    operation_id: str
    status: ArtifactStatus
    content_type: str
    data_key_ref: SecretRef | None = None
    lineage: list[LineageParent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    size: int = 0


class ArtifactDeleteResult(BaseModel):
    artifact_id: str
    status: ArtifactStatus
    reason: str | None = None


@runtime_checkable
class ArtifactBlobStore(Protocol):
    async def reserve(self, object_key: str) -> None: ...

    async def upload(self, object_key: str, payload: AsyncBytes) -> None: ...

    async def download(self, object_key: str) -> AsyncBytes: ...

    async def delete(self, object_key: str) -> None: ...


@runtime_checkable
class ArtifactMetadataStore(Protocol):
    async def reserve(
        self, artifact_id: str, owner: ArtifactOwner, operation_id: str,
        content_type: str, lineage: list[LineageParent],
    ) -> ArtifactMetadata: ...

    async def get(self, artifact_id: str) -> ArtifactMetadata | None: ...

    async def finalize(self, artifact_id: str, *, expected_status: ArtifactStatus) -> ArtifactMetadata: ...

    async def abandon(self, artifact_id: str) -> ArtifactMetadata: ...

    async def delete(self, artifact_id: str) -> ArtifactDeleteResult: ...


@runtime_checkable
class ArtifactManager(Protocol):
    async def put(
        self, operation_id: str, owner: ArtifactOwner,
        lineage: list[LineageParent], payload: AsyncBytes,
        purpose: ArtifactPurpose,
    ) -> ArtifactRef: ...

    async def get(
        self, ref: ArtifactRef, purpose: ArtifactPurpose,
    ) -> AsyncBytes: ...

    async def delete(
        self, ref: ArtifactRef, operation_id: str,
    ) -> ArtifactDeleteResult: ...

    async def put_settlement_quarantine(
        self, operation_id: str, attempt: int, generation: int,
        payload: AsyncBytes, permit: SettlementPermit,
    ) -> SettlementArtifactRef: ...
