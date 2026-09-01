"""Accounting Ports: UsageLedger + AuditStore (design §10.7, §24.3).

Both split append-only non-sensitive Fact from crypto-erasable IdentityLink. Erasure
deletes the link; the core fact is never modified or re-keyed.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.budget import BudgetUsage
from usagi_agent.types.refs import ArtifactRef, PrincipalRef
from usagi_agent.types.settlement import UsageFact, UsageIdentityLink


class AuditFact(BaseModel):
    audit_id: str
    event_type: str
    outcome: str
    risk_category: str | None = None
    amount: Decimal | None = None
    unit: str | None = None
    occurred_at: datetime
    retention_class: str


class AuditIdentityLink(BaseModel):
    audit_id: str
    tenant_id: str
    erasure_scope_id: str
    actor_ref: PrincipalRef | None = None
    resource_ref: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)


class AuditWriteDedup(BaseModel):
    tenant_id: str
    audit_operation_key_hmac: str
    audit_id: str
    erasure_scope_id: str


@runtime_checkable
class UsageLedger(Protocol):
    async def append(self, fact: UsageFact, link: UsageIdentityLink) -> str: ...

    async def get_budget_usage(self, tenant_id: str, run_id: str) -> BudgetUsage: ...


@runtime_checkable
class AuditStore(Protocol):
    async def append(
        self, audit_operation_id: str, fact: AuditFact, link: AuditIdentityLink,
    ) -> str: ...

    async def query(self, tenant_id: str, *, event_type: str | None = None) -> list[AuditFact]: ...
