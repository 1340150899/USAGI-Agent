"""Settlement / external-effect shared contract (design §10.8, §21.3).

Referenced by kernel, tools and erasure; defined centrally so modules stay decoupled.
These are pure data — no execution logic lives here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.refs import AdapterRef, SchemaRef, SecretRef

# --- Write safety / execution status enums (§21.3, §10.8) ---

WriteSafetyMode = Literal[
    "external_idempotency",
    "reconcile",
    "at_most_once_manual",
]

WriteMode = Literal["progress_write", "settlement_write"]

ExecutionStatus = Literal[
    "reserved",
    "executing",
    "settled_success",
    "settled_failure",
    "unknown",
]

AdoptionStatus = Literal["pending", "adopted", "superseded", "discarded"]

ControlKind = Literal["run", "erasure"]
ExecutionKind = Literal["model", "tool"]
SettlementKind = Literal["ordinary", "external_effect"]


class ExternalDeleteCapabilityRef(BaseModel):
    deletion_mode: Literal["delete", "unpublish", "manual_only", "irreversible_minimal"]
    delete_tool_name: str | None = None
    reconcile_tool_name: str | None = None


class ExternalEffectPolicy(BaseModel):
    """Immutable policy attached to a creates_external_resource Tool (§21.3)."""

    model_config = ConfigDict(frozen=True)

    platform: str
    locator_requirement: Literal["required", "optional", "forbidden"]
    locator_schema: SchemaRef | None = None
    scope_resolver: AdapterRef
    account_resolver: AdapterRef
    delete_capability_ref: ExternalDeleteCapabilityRef
    resource_identity_strategy: Literal["provider_id", "keyed_locator_tag", "operation_tag"]
    finality_strategy: Literal["provider_signed", "no_callback_channel", "fixed_horizon"]
    callback_horizon_seconds: int
    correlation_retention_seconds: int
    retention_class: str


class ExternalInvocationState(BaseModel):
    """Two orthogonal state axes for model/tool invocations (§10.8)."""

    execution_status: ExecutionStatus
    adoption_status: AdoptionStatus
    attempt: int
    generation: int
    effect_fingerprint: str | None = None
    response_fingerprint: str | None = None
    open_settlement_conflict_count: int = 0


class SettlementPermit(BaseModel):
    """Narrow execution-boundary credential. Stores keep a digest + write-field allowlist."""

    tenant_id: str
    control_kind: ControlKind
    control_id: str
    execution_kind: ExecutionKind
    execution_id: str
    settlement_kind: SettlementKind
    operation_id: str
    attempt: int
    generation: int
    producer_checksum: str
    external_effect_policy_checksum: str | None = None
    allowed_write_fields_digest: str
    issued_fencing_token: int
    permit_hmac: str


class DurableInvokeStartMarker(BaseModel):
    """Durable marker proving a real external call started (§10.8).

    Its existence is the sole discriminator between ``cancelled_before_invoke`` and
    ``delivery_unknown``.
    """

    tenant_id: str
    control_kind: ControlKind
    control_id: str
    execution_id: str
    operation_id: str
    attempt: int
    generation: int
    written_at: datetime


class FencingGate(BaseModel):
    """Live fencing gate passed into graph config (§10.6). Not authoritative by itself."""

    tenant_id: str
    control_kind: ControlKind
    control_id: str
    lease_owner: str
    fencing_token: int


class DestructivePermit(BaseModel):
    """Permit required to destroy a thread/scope (§10.6)."""

    tenant_id: str
    thread_id: str
    control_kind: ControlKind
    control_id: str
    destructive_operation_id: str
    expires_at: datetime
    permit_hmac: str


class UsageFact(BaseModel):
    """Append-only non-sensitive usage fact (§10.7). No tenant/run/source identity."""

    usage_event_id: str
    kind: Literal["model", "tool", "pass", "tool_call", "token", "cost"]
    amount: float
    unit: str
    phase: Literal["reserved", "settled", "released", "adjustment"]
    occurred_at: datetime


class UsageIdentityLink(BaseModel):
    """Encrypted, crypto-erasable identity mapping for a UsageFact (§10.7)."""

    usage_event_id: str
    tenant_id: str
    erasure_scope_id: str
    usage_key_hmac: str
    reservation_event_id: str | None = None
    corrects_usage_event_id: str | None = None
    correction_sequence: int = 0
    run_id: str
    source_id: str


# Sentinel re-export so callers import field types from one place.
__all__ = [
    "WriteSafetyMode",
    "WriteMode",
    "ExecutionStatus",
    "AdoptionStatus",
    "ControlKind",
    "ExecutionKind",
    "SettlementKind",
    "ExternalDeleteCapabilityRef",
    "ExternalEffectPolicy",
    "ExternalInvocationState",
    "SettlementPermit",
    "DurableInvokeStartMarker",
    "FencingGate",
    "DestructivePermit",
    "UsageFact",
    "UsageIdentityLink",
]
