"""Tool/Model execution Stores (design §18.2, §21.7, §10.8).

Both use the two orthogonal state machines: ``execution_status`` (reserved → executing →
settled_success/settled_failure/unknown) and ``adoption_status`` (pending →
adopted/superseded/discarded). First settlement is immutable; later differing outcomes
create a SettlementConflictIncident.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.refs import ArtifactRef, SettlementArtifactRef
from usagi_agent.types.settlement import (
    AdoptionStatus,
    ExecutionStatus,
    SettlementPermit,
    WriteSafetyMode,
)


class ToolExecutionRecord(BaseModel):
    execution_id: str
    tenant_id: str
    run_id: str
    tool_name: str
    idempotency_key: str
    write_safety: WriteSafetyMode | None = None
    execution_status: ExecutionStatus = "reserved"
    adoption_status: AdoptionStatus = "pending"
    attempt: int = 0
    generation: int = 0
    # Persisted observation artifact id; lets replay return the settled result
    # instead of re-executing after a crash or interrupt resume.
    observation_ref: str | None = None
    settlement_permit_digest: str | None = None
    quarantine_receipt_ref: SettlementArtifactRef | None = None
    created_at: datetime
    updated_at: datetime


class ModelInvocationRecord(BaseModel):
    logical_invocation_id: str
    tenant_id: str
    run_id: str
    pass_id: str
    node_id: str
    logical_call_no: int
    request_hash: str
    execution_status: ExecutionStatus = "reserved"
    adoption_status: AdoptionStatus = "pending"
    attempt: int = 0
    generation: int = 0
    response_ref: ArtifactRef | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class ToolExecutionStore(Protocol):
    async def reserve(self, record: ToolExecutionRecord) -> ToolExecutionRecord: ...

    async def get(self, execution_id: str) -> ToolExecutionRecord | None: ...

    async def list_by_run(self, run_id: str) -> tuple[ToolExecutionRecord, ...]: ...

    async def cas_execution_status(
        self, execution_id: str, *, expected: ExecutionStatus, new: ExecutionStatus,
    ) -> ToolExecutionRecord: ...

    async def cas_adoption_status(
        self, execution_id: str, *, expected: AdoptionStatus, new: AdoptionStatus,
    ) -> ToolExecutionRecord: ...


@runtime_checkable
class ModelInvocationStore(Protocol):
    async def reserve(self, record: ModelInvocationRecord) -> ModelInvocationRecord: ...

    async def get(self, logical_invocation_id: str) -> ModelInvocationRecord | None: ...

    async def cas_execution_status(
        self, logical_invocation_id: str, *, expected: ExecutionStatus, new: ExecutionStatus,
    ) -> ModelInvocationRecord: ...

    async def cas_adoption_status(
        self, logical_invocation_id: str, *, expected: AdoptionStatus, new: AdoptionStatus,
        response_ref: ArtifactRef | None = None,
    ) -> ModelInvocationRecord: ...
