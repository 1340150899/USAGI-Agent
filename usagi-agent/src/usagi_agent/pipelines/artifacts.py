"""Artifact serialization helpers available to business stage implementations."""
from __future__ import annotations

import hashlib
import json

from collections.abc import AsyncIterator
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.types.refs import ArtifactOwner, ArtifactRef

T = TypeVar("T", bound=BaseModel)


class ModelContextEnvelope(BaseModel):
    """Artifact passed from Context Build to Model (and back to ResultProcess)."""

    operation: Literal["normal", "compaction"] = "normal"
    messages: list[dict[str, object]] = Field(default_factory=list)
    recalled_memories: list[object] = Field(default_factory=list)
    compacted_event_ids: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0
    compacted: bool = False


class RecallBundle(BaseModel):
    """The one canonical recall-stage output artifact.

    Every recall rule serializes its hits into this shape at the Recall stage;
    ContextBuild only deserializes RecallBundle — no per-source parsing.
    Parsed candidate content cannot live in graph state because it holds references
    only, so the bundle artifact is the handoff contract.
    """

    source: str
    hits: list[dict[str, object]] = Field(default_factory=list)


class SideEffectReceipt(BaseModel):
    """Durable acknowledgement of an idempotent memory or tool side effect."""

    operation_id: str
    effect_type: str
    result_ref: str = ""


async def put_side_effect_receipt(
    manager: ArtifactManager,
    *,
    operation_id: str,
    effect_type: str,
    result_ref: str,
    tenant_id: str,
    scope_id: str,
) -> str:
    return await put_model(
        manager,
        SideEffectReceipt(
            operation_id=operation_id,
            effect_type=effect_type,
            result_ref=result_ref,
        ),
        tenant_id=tenant_id,
        scope_id=scope_id,
        operation_id=f"receipt:{operation_id}",
    )


async def put_recall_bundle(
    manager: ArtifactManager,
    *,
    source: str,
    hits: list[dict[str, object]],
    tenant_id: str,
    scope_id: str,
    run_id: str,
) -> str:
    return await put_model(
        manager,
        RecallBundle(source=source, hits=hits),
        tenant_id=tenant_id,
        scope_id=scope_id,
        operation_id=f"recall:{source}:{run_id}:" + hashlib.sha256(
            json.dumps(hits, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
    )


async def put_model(
    manager: ArtifactManager,
    model: BaseModel,
    *,
    tenant_id: str,
    scope_id: str,
    operation_id: str,
) -> str:
    payload = model.model_dump_json().encode()

    async def chunks() -> AsyncIterator[bytes]:
        yield payload

    ref = await manager.put(
        operation_id=operation_id,
        owner=ArtifactOwner(tenant_id=tenant_id, erasure_scope_id=scope_id),
        lineage=[],
        payload=chunks(),
        purpose="run_execution",
    )
    return ref.artifact_id


async def get_model(manager: ArtifactManager, artifact_id: str, cls: type[T]) -> T | None:
    if not artifact_id:
        return None
    stream = await manager.get(
        ArtifactRef(artifact_id=artifact_id, content_type="application/json"),
        "run_execution",
    )
    payload = bytearray()
    async for chunk in stream:
        payload.extend(chunk)
    return cls.model_validate_json(payload)


async def put_bytes(
    manager: ArtifactManager, payload: bytes, *, tenant_id: str, scope_id: str,
    operation_id: str,
) -> str:
    async def chunks() -> AsyncIterator[bytes]:
        yield payload

    ref = await manager.put(
        operation_id=operation_id,
        owner=ArtifactOwner(tenant_id=tenant_id, erasure_scope_id=scope_id),
        lineage=[], payload=chunks(), purpose="run_execution",
    )
    return ref.artifact_id


async def get_bytes(manager: ArtifactManager, artifact_id: str) -> bytes:
    if not artifact_id:
        return b""
    stream = await manager.get(
        ArtifactRef(artifact_id=artifact_id, content_type="application/octet-stream"),
        "run_execution",
    )
    payload = bytearray()
    async for chunk in stream:
        payload.extend(chunk)
    return bytes(payload)


async def get_text(manager: ArtifactManager, artifact_id: str) -> str:
    return (await get_bytes(manager, artifact_id)).decode("utf-8", errors="replace")
