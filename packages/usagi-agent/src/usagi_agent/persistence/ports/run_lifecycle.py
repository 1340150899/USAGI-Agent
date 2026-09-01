"""Run-lifecycle records + Store Ports (design §10.5, §10.6).

These records are the authoritative runtime truth (RunControlState, ResumeAttempt,
InterruptCredential) and the immutable execution context. They are shared across the
Kernel, the FencedCheckpointer and Erasure, so they live on the stable Port surface.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.budget import BudgetUsage
from usagi_agent.types.refs import ArtifactRef, PrincipalRef, SecretRef
from usagi_agent.types.run import CancellationReasonCode, RunStatus


class RunStartRequestRecord(BaseModel):
    tenant_id: str
    idempotency_namespace: str
    request_idempotency_key: str
    scenario_key: str
    client_request_fingerprint: str
    execution_bundle_fingerprint: str | None
    run_id: str
    input_metadata_ref: ArtifactRef | None
    status: Literal["pending", "claimed", "started"] = "pending"
    created_at: datetime


class ExecutionContextSnapshot(BaseModel):
    """Immutable execution safety context, created with the Run (§10.6)."""

    run_id: str
    thread_id: str
    scenario_key: str
    original_principal: PrincipalRef
    tenant_id: str
    authorization_scope: tuple[str, ...]
    created_at: datetime
    absolute_deadline: datetime | None = None
    bundle_checksum: str
    graph_checksum: str
    application_version: str
    secret_refs: tuple[SecretRef, ...] = Field(default_factory=tuple)


class RunControlState(BaseModel):
    """The single mutable runtime-control truth; budget/status via `version`,
    lease via separate `lease_version` (§10.6)."""

    run_id: str
    tenant_id: str
    version: int
    lease_version: int
    run_status: RunStatus
    suspended_checkpoint_id: str | None = None
    interrupt_set_digest: str | None = None
    accepted_resume_attempt_id: str | None = None
    budget_used: BudgetUsage = Field(default_factory=BudgetUsage)
    final_result_ref: ArtifactRef | None = None
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason_code: CancellationReasonCode | None = None
    cancellation_detail_ref: ArtifactRef | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    fencing_token: int = 0


class ResumeAttempt(BaseModel):
    resume_attempt_id: str
    run_id: str
    source_checkpoint_id: str
    source_interrupt_set_digest: str
    validated_payload_ref: ArtifactRef | None = None
    auth_link_id: str
    status: Literal[
        "accepted", "invoking", "applied", "reconcile_required", "failed"
    ]
    invocation_generation: int = 0
    fencing_token: int
    resulting_checkpoint_id: str | None = None
    failure_reason_code: str | None = None
    failure_phase: Literal["pre_invoke_rejected", "post_invoke_uncertain"] | None = None


class InterruptCredential(BaseModel):
    credential_id: str
    run_id: str
    interrupt_id: str
    checkpoint_id: str
    interrupt_set_digest: str
    token_digest: str
    version: int
    status: Literal["active", "consumed", "revoked", "expired"]
    delivery_status: Literal["pending", "delivered"]
    issued_at: datetime
    delivered_at: datetime | None = None
    expires_at: datetime
    consumed_by_attempt_id: str | None = None


# --- Store Ports ---

@runtime_checkable
class RunStartRequestStore(Protocol):
    async def get_by_key(
        self, tenant_id: str, idempotency_namespace: str, request_idempotency_key: str
    ) -> RunStartRequestRecord | None: ...

    async def insert(self, record: RunStartRequestRecord) -> RunStartRequestRecord: ...

    async def claim_start(
        self, run_id: str
    ) -> bool:
        """Mark a pending request as claimed by the Start Worker (idempotent)."""
        ...


@runtime_checkable
class ExecutionContextStore(Protocol):
    async def get(self, run_id: str) -> ExecutionContextSnapshot | None: ...

    async def insert(self, snapshot: ExecutionContextSnapshot) -> None: ...


@runtime_checkable
class RunControlStore(Protocol):
    async def get(self, run_id: str) -> RunControlState | None: ...

    async def create(self, state: RunControlState) -> RunControlState: ...

    async def cas_update(
        self, run_id: str, expected_version: int, new_state: RunControlState
    ) -> RunControlState:
        """CAS on ordinary `version` for status/budget transitions."""
        ...

    async def cas_lease(
        self,
        run_id: str,
        *,
        expected_lease_version: int,
        lease_owner: str | None,
        lease_expires_at: datetime | None,
        fencing_token: int,
    ) -> RunControlState:
        """CAS on independent `lease_version` for acquire/renew/release (§10.6)."""
        ...


@runtime_checkable
class ResumeAttemptStore(Protocol):
    async def get(self, resume_attempt_id: str) -> ResumeAttempt | None: ...

    async def create(self, attempt: ResumeAttempt) -> ResumeAttempt: ...

    async def cas_status(
        self, resume_attempt_id: str, expected_status: str, new_status: str,
        **updates: object,
    ) -> ResumeAttempt: ...


@runtime_checkable
class InterruptCredentialStore(Protocol):
    async def get(self, credential_id: str) -> InterruptCredential | None: ...

    async def issue(self, credential: InterruptCredential) -> InterruptCredential: ...

    async def consume(
        self, credential_id: str, *, expected_version: int, consumed_by_attempt_id: str
    ) -> InterruptCredential: ...

    async def revoke(self, credential_id: str, *, expected_version: int) -> InterruptCredential: ...


