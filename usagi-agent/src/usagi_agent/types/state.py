"""Serializable graph-state schemas and reducers."""
from __future__ import annotations

from typing import Annotated, TypedDict

from usagi_agent.types.refs import AgentResultRef, ArtifactRef


RUN_SCOPED_STATE_FIELDS = frozenset(
    {
        "run_id",
        "request_ref",
        "context_compaction_mode",
        "iteration",
        "tool_call_count",
        "reason_codes",
        "tool_observation_refs",
        "side_effect_receipt_refs",
        "final_output_ref",
        "output_schema_checksum",
        "structured_output_attempts",
        "structured_output_observation_ref",
    }
)

PASS_SCOPED_STATE_FIELDS = frozenset(
    {
        "recall_plan_ref",
        "recall_cache",
        "context_pack_ref",
        "model_request_ref",
        "context_operation",
        "model_response_ref",
        "action_type",
        "action_hash",
        "agent_action_ref",
        "tool_action_refs",
        "pass_disposition",
    }
)


def append_dedup(left: list, right: list | None) -> list:  # type: ignore[type-arg]
    """Append items while deduplicating by artifact id or scalar value."""
    if right is None:
        return list(left)
    seen = {getattr(value, "artifact_id", value) for value in left}
    output = list(left)
    for value in right:
        key = getattr(value, "artifact_id", value)
        if key not in seen:
            output.append(value)
            seen.add(key)
    return output


def last_write(left: object, right: object) -> object:
    """Use the most recent non-None graph update."""
    return right if right is not None else left


class WorkflowState(TypedDict, total=False):
    business_input_ref: ArtifactRef
    agent_result_refs: dict[str, AgentResultRef]
    workflow_status: str
    artifact_refs: list[ArtifactRef]


class AgentRunState(TypedDict, total=False):
    """The one authoritative state schema for the compiled agent graph."""

    # Run-scoped fields.
    run_id: str
    request_ref: str
    context_compaction_mode: str
    iteration: Annotated[int, last_write]
    tool_call_count: Annotated[int, last_write]
    reason_codes: Annotated[list[str], append_dedup]
    tool_observation_refs: Annotated[list[str], append_dedup]
    side_effect_receipt_refs: Annotated[list[str], append_dedup]
    final_output_ref: str
    output_schema_checksum: str
    structured_output_attempts: Annotated[int, last_write]
    structured_output_observation_ref: str

    # Pass-scoped fields, reset by PreRecall before every pass.
    recall_plan_ref: str
    recall_cache: dict[str, str]
    context_pack_ref: str
    model_request_ref: str
    context_operation: str
    model_response_ref: str
    action_type: str
    action_hash: str
    agent_action_ref: str
    tool_action_refs: Annotated[list[str], last_write]
    pass_disposition: Annotated[str, last_write]


# Compatibility names now point to the same schema instead of defining a
# second, divergent set of agent state fields.
AgentLoopState = AgentRunState
AgentPassState = AgentRunState


class ModuleState(TypedDict, total=False):
    module_input_ref: ArtifactRef
    module_output_ref: ArtifactRef
    status: str
    reason_codes: list[str]
    diagnostics_ref: ArtifactRef | None
