"""Artifact serialization helpers available to business stage implementations."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from usagi_agent.persistence.ports.artifact import ArtifactManager
from usagi_agent.types.refs import ArtifactOwner, ArtifactRef

T = TypeVar("T", bound=BaseModel)


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
