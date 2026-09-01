"""AgentAction + PassResult + ToolObservation (design §19.3, §20.3, §21.6).

The model only *proposes* an AgentAction; the EndRule executes it after Policy/Approval.
AgentAction and PassResult are persisted as Artifacts; State only keeps their Refs and
low-sensitivity ``action_type``/``disposition``/``reason_codes``.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

from usagi_agent.types.refs import (
    AgentRef,
    AgentResultRef,
    ArtifactRef,
    FinalOutputRef,
    PromptRef,
    SchemaRef,
    ToolObservationRef,
    SettlementArtifactRef,
)


class SafeErrorRecord(BaseModel):
    reason_code: str
    message: str = ""


class FinalAction(BaseModel):
    kind: Literal["final"] = "final"
    output_ref: ArtifactRef
    output_schema: SchemaRef


class ToolAction(BaseModel):
    kind: Literal["tool"] = "tool"
    tool_name: str
    tool_call_id: str
    arguments: dict[str, object] = Field(default_factory=dict)
    rationale: str | None = None


class DelegateAction(BaseModel):
    kind: Literal["delegate"] = "delegate"
    target_agent: AgentRef
    objective: str
    input: dict[str, object] = Field(default_factory=dict)
    expected_output_schema: SchemaRef


class NeedInputAction(BaseModel):
    kind: Literal["need_input"] = "need_input"
    prompt_ref: PromptRef
    input_schema: SchemaRef


class FailureAction(BaseModel):
    kind: Literal["failure"] = "failure"
    error: SafeErrorRecord
    retryable: bool = False
    source_stage: str


AgentAction = Union[FinalAction, ToolAction, DelegateAction, NeedInputAction, FailureAction]


class ToolObservation(BaseModel):
    tool_name: str
    tool_call_id: str = ""
    status: Literal["success", "denied", "failed", "unknown"]
    output: dict[str, object] | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    receipt_ref: SettlementArtifactRef | None = None
    latency_ms: int = 0


class PassResult(BaseModel):
    """EndRule's per-pass disposition; itself persisted as an Artifact (§20.3)."""

    disposition: Literal["next_pass", "run_completed", "run_failed"]
    final_output_ref: FinalOutputRef | None = None
    observation_refs: list[ToolObservationRef] = Field(default_factory=list)
    delegated_result_refs: list[AgentResultRef] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
