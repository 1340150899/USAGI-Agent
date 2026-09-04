"""AgentLoop graph state (design §8.1, §8.2).

A single merged schema used by the v1 AgentLoop + AgentPass + Rule subgraphs (shared-state
subgraph registration, §9.4). Every field is a low-sensitivity routing value or an
artifact_id string — never raw domain payload (§8.2). The State contract test enforces this.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from usagi_agent.types.state import append_dedup, last_write


class AgentRunState(TypedDict, total=False):
    # Identity / control
    run_id: str
    # Input / request
    request_ref: str
    normalized_input_ref: str
    recall_cache: dict[str, str]
    # Recall + context
    recall_plan_ref: str
    recall_bundle_ref: str
    context_pack_ref: str
    model_request_ref: str
    context_operation: str
    context_compaction_mode: str
    # Model + action
    model_response_ref: str
    action_type: str
    action_hash: str
    agent_action_ref: str
    # Per-pass list of tool action artifact ids; overwritten every pass so a
    # later pass can never replay the previous pass's actions.
    tool_action_refs: Annotated[list[str], last_write]
    # Pass result + loop control
    pass_disposition: Annotated[str, last_write]
    pass_result_ref: str
    iteration: Annotated[int, last_write]
    tool_call_count: Annotated[int, last_write]
    delegation_count: Annotated[int, last_write]
    reason_codes: Annotated[list[str], append_dedup]
    # Accumulated outputs (dedup by id)
    tool_observation_refs: Annotated[list[str], append_dedup]
    final_output_ref: str
