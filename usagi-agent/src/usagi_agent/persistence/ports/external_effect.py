"""External-effect settlement Ports.

Concise contract surface; the full settlement state machines (effect/resource aggregate,
scope links, case links, finality, conflicts) live in the tools/erasure modules. The Port
keeps modules decoupled: kernel issues SettlementPermit; tools/erasure consume it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.refs import PrincipalRef, SecretRef, SettlementArtifactRef
from usagi_agent.types.settlement import (
    ControlKind,
    ExecutionStatus,
    ExternalEffectPolicy,
    SettlementPermit,
)

EffectStatus = Literal[
    "reserved", "confirmed", "confirmed_absent", "unknown", "delete_pending", "deleted"
]
ResourceStatus = Literal[
    "delivery_pending", "cancelled_before_invoke", "observed", "delete_pending",
    "deleted", "not_found", "delivery_unknown",
    "irreversible_disclosed", "irreversible_delivery_unknown_disclosed",
]


class ExternalEffectRecord(BaseModel):
    effect_id: str
    tenant_id: str
    control_kind: ControlKind
    control_id: str
    execution_id: str
    operation_id: str
    attempt: int
    generation: int
    producer_tool_name: str
    external_effect_policy: ExternalEffectPolicy
    platform: str
    account_ref: str
    scope_link_count: int = 0
    version: int = 0
    effect_key_ref: SecretRef | None = None
    effect_key_version: int | None = None
    resource_set_status: Literal["open", "closing", "closed"] = "open"
    resource_set_closed_at: datetime | None = None
    status: EffectStatus = "reserved"
    resource_count: int = 0


class ExternalEffectResource(BaseModel):
    resource_id: str
    tenant_id: str
    effect_id: str
    resource_identity_tag: str
    status: ResourceStatus
    version: int = 0


class ExternalEffectSettlementCommand(BaseModel):
    tenant_id: str
    control_kind: ControlKind
    control_id: str
    execution_id: str
    effect_id: str
    operation_id: str
    attempt: int
    generation: int
    expected_execution_status: ExecutionStatus
    expected_effect_version: int
    outcome: Literal["success", "failure", "unknown"]
    effect_fingerprint: str
    response_fingerprint: str | None = None


@runtime_checkable
class ExternalEffectSettlementService(Protocol):
    async def settle_external_effect(
        self, command: ExternalEffectSettlementCommand, permit: SettlementPermit,
    ) -> "ExternalEffectSettlementResult": ...


class ExternalEffectSettlementResult(BaseModel):
    status: Literal["settled", "already_settled", "conflict_incident_created", "rejected"]
    execution_status: ExecutionStatus
    effect_version: int
    resource_ids: tuple[str, ...] = Field(default_factory=tuple)
    incident_ids: tuple[str, ...] = Field(default_factory=tuple)


@runtime_checkable
class ExternalEffectStore(Protocol):
    async def get_for_case(
        self, tenant_id: str, erasure_case_id: str, effect_id: str,
    ) -> ExternalEffectRecord: ...

    async def transition_resource(
        self, tenant_id: str, erasure_case_id: str, resource_id: str,
        expected_resource_version: int,
        target_status: Literal["delete_pending", "deleted", "not_found"],
        evidence_ref: SettlementArtifactRef | None, *, principal: PrincipalRef,
    ) -> ExternalEffectResource: ...

    async def close_resource_set(
        self, tenant_id: str, erasure_case_id: str, effect_id: str,
        expected_effect_version: int, *, principal: PrincipalRef,
    ) -> ExternalEffectRecord: ...

    async def finalize_effect(
        self, tenant_id: str, erasure_case_id: str, effect_id: str,
        expected_effect_version: int, *, principal: PrincipalRef,
    ) -> ExternalEffectRecord: ...
