"""InMemory implementations of the remaining Store Ports (§20-24)."""
from __future__ import annotations

import asyncio
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from usagi_agent.api.errors import CASMismatch
from usagi_agent.persistence.ports.approval import ApprovalTask
from usagi_agent.persistence.ports.erasure import (
    KeyDestructionOperation,
    LineageEdge,
)
from usagi_agent.persistence.ports.execution import (
    ModelInvocationRecord,
    ToolExecutionRecord,
)
from usagi_agent.persistence.ports.outbox import DurableOutboxEvent, InboxReceipt
from usagi_agent.persistence.ports.memory import MemoryHit, MemoryRecord
from usagi_agent.types.refs import SecretRef
from usagi_agent.types.settlement import AdoptionStatus, ExecutionStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_op: dict[str, ApprovalTask] = {}
        self._by_id: dict[str, ApprovalTask] = {}

    async def get_or_create(self, task: ApprovalTask) -> ApprovalTask:
        async with self._lock:
            if task.approval_operation_id in self._by_op:
                return self._by_op[task.approval_operation_id]
            self._by_op[task.approval_operation_id] = task
            self._by_id[task.approval_id] = task
            return task

    async def get(self, approval_id: str) -> ApprovalTask | None:
        async with self._lock:
            return self._by_id.get(approval_id)

    async def list_pending(self, run_id: str) -> tuple[ApprovalTask, ...]:
        async with self._lock:
            return tuple(
                task for task in self._by_id.values()
                if task.run_id == run_id and task.status == "pending"
            )

    async def cas_decide(
        self, approval_id: str, *, expected_version: int,
        decision: Literal["approve", "reject"], evidence_ref,
    ) -> ApprovalTask:
        async with self._lock:
            t = self._by_id.get(approval_id)
            if t is None or t.version != expected_version:
                raise CASMismatch(f"approval decide cas failed {approval_id}")
            bumped = t.model_copy(
                update={
                    "status": "approved" if decision == "approve" else "rejected",
                    "version": t.version + 1,
                    "decided_at": _now(),
                    "evidence_ref": evidence_ref,
                }
            )
            self._by_id[approval_id] = bumped
            self._by_op[bumped.approval_operation_id] = bumped
            return bumped


class InMemoryToolExecutionStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, ToolExecutionRecord] = {}

    async def reserve(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        async with self._lock:
            self._by_id[record.execution_id] = record
            return record

    async def get(self, execution_id: str) -> ToolExecutionRecord | None:
        async with self._lock:
            return self._by_id.get(execution_id)

    async def cas_execution_status(
        self, execution_id: str, *, expected: ExecutionStatus, new: ExecutionStatus,
    ) -> ToolExecutionRecord:
        async with self._lock:
            r = self._by_id.get(execution_id)
            if r is None or r.execution_status != expected:
                raise CASMismatch(f"tool execution status cas failed {execution_id}")
            bumped = r.model_copy(update={"execution_status": new, "updated_at": _now()})
            self._by_id[execution_id] = bumped
            return bumped

    async def cas_adoption_status(
        self, execution_id: str, *, expected: AdoptionStatus, new: AdoptionStatus,
    ) -> ToolExecutionRecord:
        async with self._lock:
            r = self._by_id.get(execution_id)
            if r is None or r.adoption_status != expected:
                raise CASMismatch(f"tool adoption cas failed {execution_id}")
            bumped = r.model_copy(update={"adoption_status": new, "updated_at": _now()})
            self._by_id[execution_id] = bumped
            return bumped


class InMemoryModelInvocationStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, ModelInvocationRecord] = {}

    async def reserve(self, record: ModelInvocationRecord) -> ModelInvocationRecord:
        async with self._lock:
            self._by_id[record.logical_invocation_id] = record
            return record

    async def get(self, logical_invocation_id: str) -> ModelInvocationRecord | None:
        async with self._lock:
            return self._by_id.get(logical_invocation_id)

    async def cas_execution_status(
        self, logical_invocation_id: str, *, expected: ExecutionStatus, new: ExecutionStatus,
    ) -> ModelInvocationRecord:
        async with self._lock:
            r = self._by_id.get(logical_invocation_id)
            if r is None or r.execution_status != expected:
                raise CASMismatch(f"model invocation status cas failed {logical_invocation_id}")
            bumped = r.model_copy(update={"execution_status": new, "updated_at": _now()})
            self._by_id[logical_invocation_id] = bumped
            return bumped

    async def cas_adoption_status(
        self, logical_invocation_id: str, *, expected: AdoptionStatus, new: AdoptionStatus,
        response_ref=None,
    ) -> ModelInvocationRecord:
        async with self._lock:
            r = self._by_id.get(logical_invocation_id)
            if r is None or r.adoption_status != expected:
                raise CASMismatch(f"model adoption cas failed {logical_invocation_id}")
            updates = {"adoption_status": new, "updated_at": _now()}
            if response_ref is not None:
                updates["response_ref"] = response_ref
            bumped = r.model_copy(update=updates)  # type: ignore[arg-type]
            self._by_id[logical_invocation_id] = bumped
            return bumped


class InMemoryOutboxStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._events: dict[str, DurableOutboxEvent] = {}
        self._receipts: dict[tuple[str, str], InboxReceipt] = {}

    async def enqueue(self, event: DurableOutboxEvent) -> DurableOutboxEvent:
        async with self._lock:
            self._events[event.event_id] = event
            return event

    async def claim(self, *, consumer: str, now: datetime, lease_seconds: int) -> DurableOutboxEvent | None:
        async with self._lock:
            for ev in self._events.values():
                if ev.status == "pending" and ev.available_at <= now:
                    bumped = ev.model_copy(
                        update={
                            "status": "claimed",
                            "claim_owner": consumer,
                            "claim_expires_at": now + timedelta(seconds=lease_seconds),
                            "attempt": ev.attempt + 1,
                            "version": ev.version + 1,
                        }
                    )
                    self._events[ev.event_id] = bumped
                    return bumped
            return None

    async def publish(self, event_id: str, *, expected_version: int) -> DurableOutboxEvent:
        async with self._lock:
            ev = self._events.get(event_id)
            if ev is None or ev.version != expected_version:
                raise CASMismatch(f"outbox publish cas failed {event_id}")
            bumped = ev.model_copy(
                update={"status": "published", "published_at": _now(), "version": ev.version + 1}
            )
            self._events[event_id] = bumped
            return bumped

    async def ack(self, consumer: str, event_id: str, receipt: InboxReceipt) -> InboxReceipt:
        async with self._lock:
            self._receipts[(consumer, event_id)] = receipt
            return receipt


class InMemorySecretStore:
    """Dev only: resolves SecretRef to plaintext bytes held in process memory."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, int], bytes] = {}

    def register(self, ref: SecretRef, value: bytes) -> None:
        self._values[(ref.secret_id, ref.version)] = value

    async def resolve(self, ref: SecretRef) -> bytes:
        val = self._values.get((ref.secret_id, ref.version))
        if val is None:
            raise KeyError(f"secret {ref.secret_id}@{ref.version} not registered")
        return val

    async def health(self) -> bool:
        return True


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, MemoryRecord] = {}

    async def get(self, memory_id: str) -> MemoryRecord | None:
        async with self._lock:
            return self._by_id.get(memory_id)

    async def list_by_namespace(self, tenant_id: str, namespace: str) -> list[MemoryRecord]:
        async with self._lock:
            return [
                m for m in self._by_id.values()
                if m.tenant_id == tenant_id and m.namespace == namespace
            ]

    async def insert(self, record: MemoryRecord) -> MemoryRecord:
        async with self._lock:
            self._by_id[record.memory_id] = record
            return record

    async def update_status(self, memory_id: str, *, status: str, version: int) -> MemoryRecord:
        async with self._lock:
            m = self._by_id.get(memory_id)
            if m is None or m.version != version:
                raise CASMismatch(f"memory status cas failed {memory_id}")
            bumped = m.model_copy(update={"status": status, "version": version + 1, "updated_at": _now()})
            self._by_id[memory_id] = bumped
            return bumped

    async def delete(self, memory_id: str) -> None:
        async with self._lock:
            self._by_id.pop(memory_id, None)


class InMemoryVectorStore:
    """Dev vector store: brute-force cosine over registered embeddings (placeholder vectors)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_memory: dict[str, tuple[bytes, str]] = {}  # memory_id -> (blob_ref_id, namespace)

    async def upsert(self, memory_id: str, embedding_ref) -> None:
        async with self._lock:
            self._by_memory[memory_id] = (embedding_ref.artifact_id, getattr(embedding_ref, "lineage_id", ""))

    async def search(self, embedding_ref, *, top_k: int) -> list[MemoryHit]:
        # Dev: return empty — real hybrid retrieve is a capability adapter (Layer 7).
        return []

    async def delete(self, memory_id: str) -> None:
        async with self._lock:
            self._by_memory.pop(memory_id, None)


class InMemoryLineageIndex:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._edges: list[LineageEdge] = []

    async def add_edge(self, edge: LineageEdge) -> None:
        async with self._lock:
            self._edges.append(edge)

    async def traverse(self, tenant_id: str, erasure_scope_id: str) -> list[LineageEdge]:
        async with self._lock:
            return [e for e in self._edges if e.tenant_id == tenant_id and e.erasure_scope_id == erasure_scope_id]

    async def delete_scope(self, tenant_id: str, erasure_scope_id: str) -> None:
        async with self._lock:
            self._edges = [
                e for e in self._edges
                if not (e.tenant_id == tenant_id and e.erasure_scope_id == erasure_scope_id)
            ]


class InMemoryKeyDestructionStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, KeyDestructionOperation] = {}

    async def reserve(self, operation: KeyDestructionOperation) -> KeyDestructionOperation:
        async with self._lock:
            self._by_id[operation.destruction_operation_id] = operation
            return operation

    async def get(self, destruction_operation_id: str) -> KeyDestructionOperation | None:
        async with self._lock:
            return self._by_id.get(destruction_operation_id)

    async def cas_status(
        self, destruction_operation_id: str, *, expected: str, new: str,
    ) -> KeyDestructionOperation:
        async with self._lock:
            o = self._by_id.get(destruction_operation_id)
            if o is None or o.status != expected:
                raise CASMismatch(f"key destruction cas failed {destruction_operation_id}")
            bumped = o.model_copy(update={"status": new, "version": o.version + 1})
            self._by_id[destruction_operation_id] = bumped
            return bumped


class InMemoryErasureControlStore:
    """Generic object store mirroring RunControlStore shape for the ErasureWorkflow."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_case: dict[str, object] = {}

    async def get(self, erasure_case_id: str) -> object | None:
        async with self._lock:
            return self._by_case.get(erasure_case_id)

    async def create(self, state: object) -> object:
        async with self._lock:
            cid = getattr(state, "erasure_case_id", None)
            if cid is None:
                raise ValueError("erasure state missing erasure_case_id")
            if cid in self._by_case:
                raise CASMismatch(f"erasure control {cid} exists")
            self._by_case[cid] = state
            return state

    async def cas_update(self, erasure_case_id: str, expected_version: int, new_state: object) -> object:
        async with self._lock:
            current = self._by_case.get(erasure_case_id)
            if current is None or getattr(current, "version", None) != expected_version:
                raise CASMismatch(f"erasure control cas failed {erasure_case_id}")
            self._by_case[erasure_case_id] = new_state
            return new_state

    async def cas_lease(self, erasure_case_id: str, **kw: object) -> object:
        # Lease semantics are delegated to the ErasureControl state machine (Layer 8).
        return await self.cas_update(erasure_case_id, kw.get("expected_version", 0), kw.get("new_state", self._by_case.get(erasure_case_id)))  # type: ignore[arg-type]
