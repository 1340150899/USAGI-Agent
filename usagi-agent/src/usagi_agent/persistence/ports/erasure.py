"""Erasure / Lineage Port surface.

Concise Protocols; concrete record types and state machines live in the
``usagi_agent.erasure`` module (Layer 8). These Ports keep Erasure decoupled from the
Kernel: an ErasureWorkflow runs on its own thread/lease/fencing, never borrowing the
cancelled Run's identity or checkpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from usagi_agent.types.refs import ArtifactRef


class LineageEdge(BaseModel):
    tenant_id: str
    source_ref: str
    derived_ref: str
    relation: str = "derived_from"
    erasure_scope_id: str


@runtime_checkable
class LineageIndex(Protocol):
    async def add_edge(self, edge: LineageEdge) -> None: ...

    async def traverse(self, tenant_id: str, erasure_scope_id: str) -> list[LineageEdge]:
        """Return all edges reachable from the given scope (for deletion traversal)."""
        ...

    async def delete_scope(self, tenant_id: str, erasure_scope_id: str) -> None: ...


class KeyDestructionOperation(BaseModel):
    destruction_operation_id: str
    tenant_id: str
    key_ref: str | None
    key_identity_hmac: str
    key_version: int
    key_purpose: str
    status: str = "reserved"
    next_retry_at: datetime | None = None
    version: int = 0


@runtime_checkable
class KeyDestructionStore(Protocol):
    async def reserve(self, operation: KeyDestructionOperation) -> KeyDestructionOperation: ...

    async def get(self, destruction_operation_id: str) -> KeyDestructionOperation | None: ...

    async def cas_status(
        self, destruction_operation_id: str, *, expected: str, new: str,
    ) -> KeyDestructionOperation: ...


@runtime_checkable
class ErasureControlStore(Protocol):
    """Isomorphic to RunControlStore but for the independent ErasureWorkflow."""

    async def get(self, erasure_case_id: str) -> object | None: ...

    async def create(self, state: object) -> object: ...

    async def cas_update(self, erasure_case_id: str, expected_version: int, new_state: object) -> object: ...

    async def cas_lease(self, erasure_case_id: str, **kw: object) -> object: ...


@runtime_checkable
class ErasureCaseService(Protocol):
    async def request_erasure(
        self, tenant_id: str, scope_ref: str, request_idempotency_key: str,
    ) -> ArtifactRef: ...

    async def get_case(self, erasure_case_id: str) -> object: ...
