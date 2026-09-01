"""InMemory run-lifecycle stores with correct CAS / lease semantics (§10.5, §10.6)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from usagi_agent.api.errors import CASMismatch, IdempotencyConflictError
from usagi_agent.persistence.ports.run_lifecycle import (
    ExecutionContextSnapshot,
    ExecutionContextStore,
    InterruptCredential,
    InterruptCredentialStore,
    ResumeAttempt,
    ResumeAttemptStore,
    RunControlState,
    RunControlStore,
    RunStartRequestRecord,
    RunStartRequestStore,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryRunStartRequestStore(RunStartRequestStore):
    """Enforces UNIQUE(tenant, namespace, key); replay returns original record."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_key: dict[tuple[str, str, str], RunStartRequestRecord] = {}
        self._by_run: dict[str, RunStartRequestRecord] = {}

    async def get_by_key(
        self, tenant_id: str, idempotency_namespace: str, request_idempotency_key: str
    ) -> RunStartRequestRecord | None:
        async with self._lock:
            return self._by_key.get((tenant_id, idempotency_namespace, request_idempotency_key))

    async def insert(self, record: RunStartRequestRecord) -> RunStartRequestRecord:
        async with self._lock:
            key = (record.tenant_id, record.idempotency_namespace, record.request_idempotency_key)
            if key in self._by_key:
                # Same key, same fingerprint -> replay (return original); else conflict.
                existing = self._by_key[key]
                if existing.client_request_fingerprint == record.client_request_fingerprint:
                    return existing
                raise IdempotencyConflictError(
                    f"idempotency conflict for {key!r}", reason_code="run.idempotency_conflict"
                )
            self._by_key[key] = record
            self._by_run[record.run_id] = record
            return record

    async def claim_start(self, run_id: str) -> bool:
        async with self._lock:
            rec = self._by_run.get(run_id)
            if rec is None:
                return False
            if rec.status == "started":
                return True
            rec = rec.model_copy(update={"status": "claimed"})
            self._by_run[run_id] = rec
            self._by_key[(rec.tenant_id, rec.idempotency_namespace, rec.request_idempotency_key)] = rec
            return True


class InMemoryExecutionContextStore(ExecutionContextStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_run: dict[str, ExecutionContextSnapshot] = {}

    async def get(self, run_id: str) -> ExecutionContextSnapshot | None:
        async with self._lock:
            return self._by_run.get(run_id)

    async def insert(self, snapshot: ExecutionContextSnapshot) -> None:
        async with self._lock:
            if snapshot.run_id in self._by_run:
                raise CASMismatch(f"execution context {snapshot.run_id} already exists")
            self._by_run[snapshot.run_id] = snapshot  # immutable


class InMemoryRunControlStore(RunControlStore):
    """Ordinary `version` for status/budget; independent `lease_version` for lease."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_run: dict[str, RunControlState] = {}

    async def get(self, run_id: str) -> RunControlState | None:
        async with self._lock:
            return self._by_run.get(run_id)

    async def create(self, state: RunControlState) -> RunControlState:
        async with self._lock:
            if state.run_id in self._by_run:
                raise CASMismatch(f"run_control {state.run_id} already exists")
            self._by_run[state.run_id] = state
            return state

    async def cas_update(
        self, run_id: str, expected_version: int, new_state: RunControlState
    ) -> RunControlState:
        async with self._lock:
            current = self._by_run.get(run_id)
            if current is None or current.version != expected_version:
                raise CASMismatch(
                    f"cas_update {run_id}: expected version {expected_version}"
                )
            bumped = new_state.model_copy(update={"version": current.version + 1})
            self._by_run[run_id] = bumped
            return bumped

    async def cas_lease(
        self,
        run_id: str,
        *,
        expected_lease_version: int,
        lease_owner: str | None,
        lease_expires_at: datetime | None,
        fencing_token: int,
    ) -> RunControlState:
        async with self._lock:
            current = self._by_run.get(run_id)
            if current is None or current.lease_version != expected_lease_version:
                raise CASMismatch(
                    f"cas_lease {run_id}: expected lease_version {expected_lease_version}"
                )
            bumped = current.model_copy(
                update={
                    "lease_version": current.lease_version + 1,
                    "lease_owner": lease_owner,
                    "lease_expires_at": lease_expires_at,
                    "fencing_token": fencing_token,
                }
            )
            self._by_run[run_id] = bumped
            return bumped


class InMemoryResumeAttemptStore(ResumeAttemptStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, ResumeAttempt] = {}

    async def get(self, resume_attempt_id: str) -> ResumeAttempt | None:
        async with self._lock:
            return self._by_id.get(resume_attempt_id)

    async def create(self, attempt: ResumeAttempt) -> ResumeAttempt:
        async with self._lock:
            if attempt.resume_attempt_id in self._by_id:
                raise CASMismatch(f"resume_attempt {attempt.resume_attempt_id} exists")
            self._by_id[attempt.resume_attempt_id] = attempt
            return attempt

    async def cas_status(
        self, resume_attempt_id: str, expected_status: str, new_status: str,
        **updates: object,
    ) -> ResumeAttempt:
        async with self._lock:
            current = self._by_id.get(resume_attempt_id)
            if current is None or current.status != expected_status:
                raise CASMismatch(f"resume_attempt status cas failed {resume_attempt_id}")
            bumped = current.model_copy(update={"status": new_status, **updates})  # type: ignore[arg-type]
            self._by_id[resume_attempt_id] = bumped
            return bumped


class InMemoryInterruptCredentialStore(InterruptCredentialStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: dict[str, InterruptCredential] = {}

    async def get(self, credential_id: str) -> InterruptCredential | None:
        async with self._lock:
            return self._by_id.get(credential_id)

    async def issue(self, credential: InterruptCredential) -> InterruptCredential:
        async with self._lock:
            self._by_id[credential.credential_id] = credential
            return credential

    async def consume(
        self, credential_id: str, *, expected_version: int, consumed_by_attempt_id: str
    ) -> InterruptCredential:
        async with self._lock:
            c = self._by_id.get(credential_id)
            if c is None or c.version != expected_version:
                raise CASMismatch(f"credential consume cas failed {credential_id}")
            if c.status != "active":
                raise CASMismatch(f"credential {credential_id} not active: {c.status}")
            bumped = c.model_copy(
                update={
                    "status": "consumed",
                    "version": c.version + 1,
                    "consumed_by_attempt_id": consumed_by_attempt_id,
                }
            )
            self._by_id[credential_id] = bumped
            return bumped

    async def revoke(self, credential_id: str, *, expected_version: int) -> InterruptCredential:
        async with self._lock:
            c = self._by_id.get(credential_id)
            if c is None or c.version != expected_version:
                raise CASMismatch(f"credential revoke cas failed {credential_id}")
            bumped = c.model_copy(update={"status": "revoked", "version": c.version + 1})
            self._by_id[credential_id] = bumped
            return bumped
