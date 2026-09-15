"""Run lifecycle public types.

These are the stable ``RunOutcome`` projection and resume envelope contract. ``RunOptions``
is ``extra="forbid"``: clients cannot self-report identity or override Model/Tool/Memory/
Rule/Prompt/Pipeline configuration.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from usagi_agent.types.refs import ArtifactRef
from usagi_agent.types.content import ContentPart


class CancellationReasonCode(str, Enum):
    USER_REQUEST = "user_request"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    ADMINISTRATIVE = "administrative"
    ERASURE_REQUESTED = "erasure_requested"
    POLICY_REVOKED = "policy_revoked"


# Internal RunControl status; the public RunOutcome below is its projection.
RunStatus = Literal[
    "running",
    "suspended",
    "resume_accepted",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
]


class RunOptions(BaseModel):
    """Run control only; no self-reported identity, no config override."""

    model_config = ConfigDict(extra="forbid")

    absolute_deadline: datetime | None = None
    cancellation_reason_code: CancellationReasonCode | None = None
    delegation_ref: str | None = None
    trace_parent: str | None = Field(default=None, description="W3C traceparent, propagation only.")
    context_compaction: Literal["auto", "force"] = Field(
        default="auto",
        description="Use threshold-based compaction or force one safe compaction attempt.",
    )


# --- RunOutcome variants ---

class Running(BaseModel):
    kind: Literal["running"] = "running"
    run_id: str
    started_at: datetime
    scenario_key: str


class InterruptDescriptor(BaseModel):
    interrupt_id: str
    kind: Literal["approval", "user_input", "external_event"]
    checkpoint_id: str
    expected_schema_checksum: str | None = None
    token_delivery: Literal["issued", "already_delivered", "reissue_required"]


class Suspended(BaseModel):
    kind: Literal["suspended"] = "suspended"
    run_id: str
    checkpoint_id: str
    reason_code: Literal["interrupt", "manual_required"]
    interrupts: list[InterruptDescriptor]


class Resuming(BaseModel):
    kind: Literal["resuming"] = "resuming"
    run_id: str
    resume_attempt_id: str
    source_checkpoint_id: str
    stage: Literal["accepted", "invoking", "reconciling"]


class Cancelling(BaseModel):
    kind: Literal["cancelling"] = "cancelling"
    run_id: str
    requested_at: datetime
    reason_code: CancellationReasonCode
    in_flight_operations: int
    unresolved_operations: int


class Completed(BaseModel):
    kind: Literal["completed"] = "completed"
    run_id: str
    completed_at: datetime
    result_ref: ArtifactRef


class Failed(BaseModel):
    kind: Literal["failed"] = "failed"
    run_id: str
    failed_at: datetime
    reason_codes: list[str]
    failure_detail_ref: ArtifactRef | None = None


class Cancelled(BaseModel):
    kind: Literal["cancelled"] = "cancelled"
    run_id: str
    cancelled_at: datetime
    reason_code: CancellationReasonCode
    cancellation_detail_ref: ArtifactRef | None = None


RunOutcome = Union[
    Running, Suspended, Resuming, Cancelling, Completed, Failed, Cancelled
]


class RunHandle(BaseModel):
    """Stable handle returned by start/resume/cancel; terminal result via get_run/stream."""

    run_id: str
    thread_id: str
    scenario_key: str
    outcome: RunOutcome
    created_at: datetime


class RunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_key: str
    request_idempotency_key: str
    input: BaseModel
    content_parts: list[ContentPart] = Field(default_factory=list)
    options: RunOptions = Field(default_factory=RunOptions)


class StructuredRunResult(BaseModel):
    type: Literal["result"] = "result"
    subtype: Literal["success"] = "success"
    is_error: Literal[False] = False
    run_id: str
    schema_name: str
    schema_checksum: str
    structured_output: dict[str, object]


class StructuredRunError(BaseModel):
    type: Literal["result"] = "result"
    subtype: Literal[
        "error_during_execution",
        "error_invalid_output_schema",
        "error_max_structured_output_retries",
        "error_missing_structured_output",
    ]
    is_error: Literal[True] = True
    run_id: str
    reason_codes: list[str]


class RunInputEnvelope(BaseModel):
    """The input-bearing subset loaded from the complete request Artifact."""

    input: dict[str, object] = Field(default_factory=dict)
    content_parts: list[ContentPart] = Field(default_factory=list)
    options: RunOptions = Field(default_factory=RunOptions)


class RunEvent(BaseModel):
    event_type: str
    run_id: str
    occurred_at: datetime
    payload_ref: ArtifactRef | None = None
    reason_codes: list[str] = Field(default_factory=list)


class ResumeTokenEnvelope(BaseModel):
    run_id: str
    interrupt_id: str
    checkpoint_id: str
    credential_version: int
    resume_token: SecretStr


# --- ResumeEnvelope: discriminated union, never self-reported actor ---

class ResumeBase(BaseModel):
    interrupt_id: str
    expected_checkpoint_id: str
    resume_token: SecretStr


class ApprovalResume(ResumeBase):
    kind: Literal["approval"] = "approval"
    approval_id: str
    expected_approval_version: int
    approval_scope: tuple[str, ...]
    action_hash: str
    decision: Literal["approve", "reject"]


class UserInputResume(ResumeBase):
    kind: Literal["user_input"] = "user_input"
    input_schema_checksum: str
    input: dict


class ExternalEventResume(ResumeBase):
    kind: Literal["external_event"] = "external_event"
    event_id: str
    event_type: str
    payload_ref: ArtifactRef


ResumeEnvelope = Annotated[
    Union[ApprovalResume, UserInputResume, ExternalEventResume],
    Field(discriminator="kind"),
]
