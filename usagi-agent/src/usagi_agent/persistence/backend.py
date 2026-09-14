"""SQLite persistence initialization.

``PersistenceInitializer`` is the second node of the init tree (after observability). It
constructs an :class:`InfrastructurePorts` bundle backed by one SQLite database.
Stores are *constructed* here only; no Run-time execution logic lives in this module.

The in-memory classes imported below are reference state-machine implementations wrapped
by transactional SQLite adapters; they are not a selectable persistence backend.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar, cast

from langgraph.checkpoint.base import BaseCheckpointSaver

from usagi_agent.observability import ObservabilityProvider
from usagi_agent.persistence.inmemory import (
    InMemoryArtifactMetadataStore,
    InMemoryAuditStore,
    InMemoryErasureControlStore,
    InMemoryExecutionContextStore,
    InMemoryInterruptCredentialStore,
    InMemoryKeyDestructionStore,
    InMemoryLineageIndex,
    InMemoryMemoryStore,
    InMemoryModelInvocationStore,
    InMemoryOutboxStore,
    InMemoryApprovalStore,
    InMemoryResumeAttemptStore,
    InMemoryRunStartRequestStore,
    InMemorySecretStore,
    InMemoryToolExecutionStore,
    InMemoryUsageLedger,
    InMemoryVectorStore,
)
from usagi_agent.persistence.ports.artifact import (
    ArtifactBlobStore,
    ArtifactManager,
    ArtifactMetadataStore,
)
from usagi_agent.persistence.ports.erasure import (
    ErasureControlStore,
    KeyDestructionStore,
    LineageIndex,
)
from usagi_agent.persistence.ports.memory import MemoryStore, VectorStore
from usagi_agent.persistence.ports.outbox import DurableOutboxEvent, EventBus, OutboxStore
from usagi_agent.persistence.ports.run_lifecycle import (
    ExecutionContextStore,
    InterruptCredentialStore,
    ResumeAttemptStore,
    RunControlStore,
    RunStartRequestStore,
)
from usagi_agent.persistence.ports.accounting import AuditStore, UsageLedger
from usagi_agent.persistence.ports.approval import ApprovalStore
from usagi_agent.persistence.ports.execution import (
    ModelInvocationStore,
    ToolExecutionStore,
)
from usagi_agent.persistence.ports.secret import SecretStore
from usagi_agent.persistence.ports.session import SessionStore
from usagi_agent.types.settlement import FencingGate

GateVerifier = Callable[[FencingGate], Awaitable[bool]]
StoreT = TypeVar("StoreT")


def _sqlite_store(path: str, name: str, factory: Callable[[], StoreT]) -> StoreT:
    """Create a durable proxy while preserving its wrapped store's static type."""
    from usagi_agent.persistence.sqlite.stores import SqliteStore

    return cast(StoreT, SqliteStore(path, name, factory))


@dataclass
class InfrastructurePorts:
    """Bundle of all store instances injected across the framework at Bootstrap."""

    checkpointer: BaseCheckpointSaver
    run_start_request_store: RunStartRequestStore
    execution_context_store: ExecutionContextStore
    run_control_store: RunControlStore
    resume_attempt_store: ResumeAttemptStore
    interrupt_credential_store: InterruptCredentialStore
    usage_ledger: UsageLedger
    audit_store: AuditStore
    approval_store: ApprovalStore
    tool_execution_store: ToolExecutionStore
    model_invocation_store: ModelInvocationStore
    outbox_store: OutboxStore
    event_bus: EventBus
    secret_store: SecretStore
    session_store: SessionStore
    artifact_manager: ArtifactManager
    artifact_metadata_store: ArtifactMetadataStore
    artifact_blob_store: ArtifactBlobStore
    memory_store: MemoryStore
    vector_store: VectorStore
    lineage_index: LineageIndex
    key_destruction_store: KeyDestructionStore
    erasure_control_store: ErasureControlStore
    # Run-domain fencing gate verifier consumed by the checkpointer.
    run_gate_verifier: GateVerifier | None = None


def make_run_gate_verifier(run_control_store: RunControlStore) -> GateVerifier:
    """Build the async gate verifier the FencedCheckpointer calls on every write.

    Verifies: status in (running, resume_accepted), lease_owner matches,
    DB-clock expiry not passed, fencing token matches the live RunControl record.
    """

    async def _verify(gate: FencingGate) -> bool:
        state = await run_control_store.get(gate.control_id)
        if state is None:
            return False
        if state.run_status not in ("running", "resume_accepted"):
            return False
        if state.lease_owner != gate.lease_owner:
            return False
        if state.fencing_token != gate.fencing_token:
            return False
        if state.lease_expires_at is None or state.lease_expires_at <= datetime.now(timezone.utc):
            return False
        return True

    return _verify


class InMemoryEventBus:
    """Minimal in-process event bus for the dev backend."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[DurableOutboxEvent]]] = {}

    async def subscribe(self, event_type: str) -> AsyncIterator[DurableOutboxEvent]:
        queue: asyncio.Queue[DurableOutboxEvent] = asyncio.Queue()
        subscribers = self._subscribers.setdefault(event_type, [])
        subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(event_type, None)

    async def publish(self, event: DurableOutboxEvent) -> None:
        for queue in tuple(self._subscribers.get(event.event_type, ())):
            await queue.put(event)


class PersistenceInitializer:
    """Init-tree node: constructs the InfrastructurePorts bundle for the chosen backend."""

    @staticmethod
    def init(settings, observability: ObservabilityProvider) -> InfrastructurePorts:
        return PersistenceInitializer._build_sqlite(settings)

    @staticmethod
    def _build_sqlite(settings) -> InfrastructurePorts:
        # Durable checkpointer + RunControlStore share one DB so the gate the checkpointer
        # verifies (SQL) reads authoritative rows written by the store.
        from usagi_agent.persistence.sqlite.fenced_checkpointer import SqliteFencedCheckpointer
        from usagi_agent.persistence.sqlite.run_control_store import SqliteRunControlStore
        from usagi_agent.persistence.sqlite.session_store import SqliteSessionStore

        import sqlite3
        from usagi_agent.persistence.sqlite.schema import apply_schema

        for configured_path in settings.all_sqlite_paths():
            with sqlite3.connect(configured_path) as connection:
                apply_schema(connection)
        db_path = settings.selected_sqlite_path()
        assert db_path is not None
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        run_control_store = SqliteRunControlStore(db_path)
        checkpointer = SqliteFencedCheckpointer(db_path)  # verifies gate via SQL internally
        gate_verifier = make_run_gate_verifier(run_control_store)

        from usagi_agent.persistence.sqlite.stores import SqliteBlobStore, SqliteArtifactManager
        blob = SqliteBlobStore(db_path)
        meta = _sqlite_store(db_path, "artifact_metadata", InMemoryArtifactMetadataStore)
        artifact_manager = SqliteArtifactManager(meta, blob)

        # Persist the reference state machines transactionally. EventBus remains
        # an in-process hint; durable stores are authoritative during recovery.
        return InfrastructurePorts(
            checkpointer=checkpointer,
            run_start_request_store=_sqlite_store(db_path, "run_start_request_store", InMemoryRunStartRequestStore),
            execution_context_store=_sqlite_store(db_path, "execution_context_store", InMemoryExecutionContextStore),
            run_control_store=run_control_store,
            resume_attempt_store=_sqlite_store(db_path, "resume_attempt_store", InMemoryResumeAttemptStore),
            interrupt_credential_store=_sqlite_store(db_path, "interrupt_credential_store", InMemoryInterruptCredentialStore),
            usage_ledger=_sqlite_store(db_path, "usage_ledger", InMemoryUsageLedger),
            audit_store=_sqlite_store(db_path, "audit_store", InMemoryAuditStore),
            approval_store=_sqlite_store(db_path, "approval_store", InMemoryApprovalStore),
            tool_execution_store=_sqlite_store(db_path, "tool_execution_store", InMemoryToolExecutionStore),
            model_invocation_store=_sqlite_store(db_path, "model_invocation_store", InMemoryModelInvocationStore),
            outbox_store=_sqlite_store(db_path, "outbox_store", InMemoryOutboxStore),
            event_bus=InMemoryEventBus(),
            secret_store=_sqlite_store(db_path, "secret_store", InMemorySecretStore),
            session_store=SqliteSessionStore(db_path),
            artifact_manager=artifact_manager,
            artifact_metadata_store=meta,
            artifact_blob_store=blob,
            memory_store=_sqlite_store(db_path, "memory_store", InMemoryMemoryStore),
            vector_store=_sqlite_store(db_path, "vector_store", InMemoryVectorStore),
            lineage_index=_sqlite_store(db_path, "lineage_index", InMemoryLineageIndex),
            key_destruction_store=_sqlite_store(db_path, "key_destruction_store", InMemoryKeyDestructionStore),
            erasure_control_store=_sqlite_store(db_path, "erasure_control_store", InMemoryErasureControlStore),
            run_gate_verifier=gate_verifier,
        )
