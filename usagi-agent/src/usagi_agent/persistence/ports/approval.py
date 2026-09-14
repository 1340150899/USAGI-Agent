"""ApprovalStore Port + ApprovalTask.

``approval_operation_id`` and the business ``(interrupt_id, action_hash)`` are unique
across all states; replay get-or-create returns the original terminal state, never a new
pending task. Decisions use expected-version CAS and are terminal.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from usagi_agent.types.refs import ArtifactRef

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "cancelled"]


class ApprovalTask(BaseModel):
    approval_id: str
    run_id: str
    session_id: str | None = None
    approval_operation_id: str
    interrupt_id: str
    action_hash: str
    approval_scope: tuple[str, ...] = Field(default_factory=tuple)
    tool_name: str | None = None
    arguments_ref: ArtifactRef | None = None
    approval_generation: int = 0
    status: ApprovalStatus
    version: int
    created_at: datetime
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    evidence_ref: ArtifactRef | None = None


@runtime_checkable
class ApprovalStore(Protocol):
    async def get_or_create(self, task: ApprovalTask) -> ApprovalTask:
        """Idempotent get-or-create by approval_operation_id; returns original on replay."""
        ...

    async def get(self, approval_id: str) -> ApprovalTask | None: ...

    async def list_pending(self, run_id: str) -> tuple[ApprovalTask, ...]: ...

    async def list_pending_by_session(self, session_id: str) -> tuple[ApprovalTask, ...]: ...

    async def cas_decide(
        self, approval_id: str, *, expected_version: int,
        decision: Literal["approve", "reject"], evidence_ref: ArtifactRef | None,
    ) -> ApprovalTask: ...
