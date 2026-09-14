"""ErasureControl state.

Isomorphic to RunControlState but for the independent ErasureWorkflow: it does NOT reuse
the cancelled Run's identity, checkpoint, lease or fencing token.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ErasureStatus = Literal[
    "preflight_retryable", "pending", "running", "suspended", "resume_accepted", "unknown",
    "reconciling", "blocked", "manual_required", "completed", "failed",
]


class ErasureControlState(BaseModel):
    erasure_case_id: str
    tenant_id: str
    target_scope_ref: str
    version: int = 0
    lease_version: int = 0
    status: ErasureStatus = "pending"
    tombstone_committed_at: datetime | None = None
    next_retry_at: datetime | None = None
    suspended_checkpoint_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    fencing_token: int = 0
    receipt_ref: str | None = None


class ErasureReceipt(BaseModel):
    erasure_case_id: str
    completed_at: datetime
    scope_deleted: list[str] = Field(default_factory=list)
    keys_destroyed: list[str] = Field(default_factory=list)
    threads_deleted: list[str] = Field(default_factory=list)
