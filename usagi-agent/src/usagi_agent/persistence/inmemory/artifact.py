"""InMemory Artifact data plane: metadata state machine + blob + manager."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from usagi_agent.api.errors import CASMismatch
from usagi_agent.persistence.ports.artifact import (
    ArtifactBlobStore,
    ArtifactDeleteResult,
    ArtifactManager,
    ArtifactMetadata,
    ArtifactMetadataStore,
    ArtifactOwner,
    ArtifactPurpose,
    ArtifactStatus,
    AsyncBytes,
)
from usagi_agent.types.refs import ArtifactRef, LineageParent, SettlementArtifactRef
from usagi_agent.types.settlement import SettlementPermit


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_artifact_id() -> str:
    return "art_" + uuid.uuid4().hex


async def _consume(payload: AsyncBytes) -> bytes:
    return b"".join([chunk async for chunk in payload])


def _async_from_bytes(data: bytes) -> AsyncBytes:
    async def _gen() -> AsyncIterator[bytes]:
        yield data

    return _gen()


class InMemoryArtifactBlobStore(ArtifactBlobStore):
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def reserve(self, object_key: str) -> None:
        self._blobs.setdefault(object_key, b"")

    async def upload(self, object_key: str, payload: AsyncBytes) -> None:
        self._blobs[object_key] = await _consume(payload)

    async def download(self, object_key: str) -> AsyncBytes:
        return _async_from_bytes(self._blobs.get(object_key, b""))

    async def delete(self, object_key: str) -> None:
        self._blobs.pop(object_key, None)


class InMemoryArtifactMetadataStore(ArtifactMetadataStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, ArtifactMetadata] = {}

    async def reserve(
        self, artifact_id: str, owner: ArtifactOwner, operation_id: str,
        content_type: str, lineage: list[LineageParent],
    ) -> ArtifactMetadata:
        now = _now()
        meta = ArtifactMetadata(
            artifact_id=artifact_id,
            tenant_id=owner.tenant_id,
            owner=owner,
            operation_id=operation_id,
            status="reserved",
            content_type=content_type,
            lineage=lineage,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._by_id[artifact_id] = meta
            return meta

    async def get(self, artifact_id: str) -> ArtifactMetadata | None:
        async with self._lock:
            return self._by_id.get(artifact_id)

    async def finalize(self, artifact_id: str, *, expected_status: ArtifactStatus) -> ArtifactMetadata:
        async with self._lock:
            m = self._by_id.get(artifact_id)
            if m is None or m.status != expected_status:
                raise CASMismatch(f"finalize {artifact_id}: expected {expected_status}, got {m.status if m else None}")
            bumped = m.model_copy(update={"status": "available", "updated_at": _now()})
            self._by_id[artifact_id] = bumped
            return bumped

    async def abandon(self, artifact_id: str) -> ArtifactMetadata:
        async with self._lock:
            m = self._by_id.get(artifact_id)
            if m is None:
                raise CASMismatch(f"abandon {artifact_id}: missing")
            bumped = m.model_copy(update={"status": "abandoned", "updated_at": _now()})
            self._by_id[artifact_id] = bumped
            return bumped

    async def delete(self, artifact_id: str) -> ArtifactDeleteResult:
        async with self._lock:
            m = self._by_id.get(artifact_id)
            if m is None:
                return ArtifactDeleteResult(artifact_id=artifact_id, status="deleted", reason="missing")
            bumped = m.model_copy(update={"status": "deleted", "updated_at": _now()})
            self._by_id[artifact_id] = bumped
            return ArtifactDeleteResult(artifact_id=artifact_id, status="deleted")


class InMemoryArtifactManager(ArtifactManager):
    """Orchestrates reserve metadata -> upload blob -> finalize."""

    def __init__(
        self, metadata_store: ArtifactMetadataStore, blob_store: ArtifactBlobStore,
    ) -> None:
        self._meta = metadata_store
        self._blob = blob_store

    async def put(
        self, operation_id: str, owner: ArtifactOwner,
        lineage: list[LineageParent], payload: AsyncBytes,
        purpose: ArtifactPurpose,
    ) -> ArtifactRef:
        artifact_id = _new_artifact_id()
        await self._meta.reserve(artifact_id, owner, operation_id, "application/octet-stream", lineage)
        await self._blob.upload(artifact_id, payload)
        # mark uploaded then finalize (available)
        await self._meta.finalize(artifact_id, expected_status="reserved")
        return ArtifactRef(artifact_id=artifact_id, content_type="application/octet-stream")

    async def get(self, ref: ArtifactRef, purpose: ArtifactPurpose) -> AsyncBytes:
        meta = await self._meta.get(ref.artifact_id)
        if meta is None or meta.status not in ("available", "adopted_available"):
            raise PermissionError(f"artifact {ref.artifact_id} not available")
        return await self._blob.download(ref.artifact_id)

    async def delete(self, ref: ArtifactRef, operation_id: str) -> ArtifactDeleteResult:
        await self._blob.delete(ref.artifact_id)
        return await self._meta.delete(ref.artifact_id)

    async def put_settlement_quarantine(
        self, operation_id: str, attempt: int, generation: int,
        payload: AsyncBytes, permit: SettlementPermit,
    ) -> SettlementArtifactRef:
        artifact_id = "sq_" + uuid.uuid4().hex
        await self._blob.upload(artifact_id, payload)
        # Settlement quarantine is unreadable, short TTL, no business lineage.
        return SettlementArtifactRef(
            artifact_id=artifact_id, content_type="application/x-settlement-quarantine", lineage_id=None
        )
