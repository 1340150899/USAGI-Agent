"""Execution-context Ports (design §11.6, §24.3).

Narrow contexts passed into capability adapters. Shared by every capability Port, so
defined in their own file rather than mixed into any one capability.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from usagi_agent.types.refs import PrincipalRef

HealthStatus = Literal["healthy", "degraded", "unhealthy"]


class GovernedExecutionContext(BaseModel):
    """Adapted-to by RunContext / ErasureExecutionContext / etc. (§24.3)."""

    tenant_id: str
    principal: PrincipalRef
    authorization_scope: tuple[str, ...]
    absolute_deadline: str | None = None
    control_kind: Literal["run", "erasure", "memory_maintenance", "reconciliation"]
    control_id: str
    fencing_token: int


class DataAccessContext(BaseModel):
    execution: GovernedExecutionContext
    data_domain: Literal["run", "erasure_case", "memory_maintenance", "settlement"]
    erasure_scope_id: str
    purpose_namespace: str


class ToolContext(BaseModel):
    execution: GovernedExecutionContext
    account_ref: str | None = None


class StageContext(BaseModel):
    execution: GovernedExecutionContext
