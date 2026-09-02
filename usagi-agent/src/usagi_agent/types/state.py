"""State schemas (design §8.1, §8.2).

State holds ONLY serializable low-sensitivity routing fields and ArtifactRef. Raw chat,
images, prompts, model responses, ContextPack, AgentAction, Tool params/output and
FinalOutput must never enter checkpoint as embedded objects. The State contract test
enforces this (§8.2, §31).
"""
from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from usagi_agent.types.budget import BudgetSummary
from usagi_agent.types.refs import (
    AgentActionRef,
    AgentResultRef,
    ArtifactRef,
    ContextPackRef,
    FinalOutputRef,
    ModelResponseRef,
    ModelRequestRef,
    PassResultRef,
    ToolObservationRef,
)


# --- Reducers (pure functions; the Compiler registers them under ReducerRef strings) ---

def append_dedup(left: list, right: list | None) -> list:  # type: ignore[type-arg]
    """Append new items, deduplicating by artifact_id (or value for scalars)."""
    if right is None:
        return list(left)
    seen = {getattr(v, "artifact_id", v) for v in left}
    out = list(left)
    for v in right:
        key = getattr(v, "artifact_id", v)
        if key not in seen:
            out.append(v)
            seen.add(key)
    return out


def last_write(left: object, right: object) -> object:
    """Last-write-wins reducer for scalar routing fields."""
    return right if right is not None else left


# --- State TypedDicts (total=False: every field is optional) ---

class WorkflowState(TypedDict, total=False):
    business_input_ref: ArtifactRef
    agent_result_refs: dict[str, AgentResultRef]
    workflow_status: str
    artifact_refs: list[ArtifactRef]


class AgentLoopState(TypedDict, total=False):
    request_ref: ArtifactRef
    recall_cache: dict[str, ArtifactRef]
    tool_observation_refs: Annotated[list[ToolObservationRef], append_dedup]
    delegated_result_refs: Annotated[list[AgentResultRef], append_dedup]
    iteration: int
    budget_summary: BudgetSummary
    pass_disposition: Literal["next_pass", "run_completed", "run_failed"]
    pass_result_ref: PassResultRef
    final_output_ref: FinalOutputRef | None


class AgentPassState(TypedDict, total=False):
    normalized_input_ref: ArtifactRef
    recall_plan_ref: ArtifactRef
    recall_bundle_ref: ArtifactRef
    context_pack_ref: ContextPackRef
    model_request_ref: ModelRequestRef
    model_response_ref: ModelResponseRef
    action_type: Literal["final", "tool", "delegate", "need_input", "failure"]
    action_hash: str
    agent_action_ref: AgentActionRef
    pass_disposition: Literal["next_pass", "run_completed", "run_failed"]
    pass_result_ref: PassResultRef


class ModuleState(TypedDict, total=False):
    """Module-private state base; concrete Module Pipelines extend with their own fields."""

    module_input_ref: ArtifactRef
    module_output_ref: ArtifactRef
    status: str
    reason_codes: list[str]
    diagnostics_ref: ArtifactRef | None
