"""Stage-specific Rule inputs and outputs, independent of LangGraph state."""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

class PreRecallStagePatch(TypedDict, total=False):
    context_compaction_mode: str
    recall_plan_ref: str
    recall_cache: dict[str, str]
    context_pack_ref: str
    model_request_ref: str
    context_operation: str
    model_response_ref: str
    action_type: str
    action_hash: str
    agent_action_ref: str
    tool_action_refs: list[str]
    pass_disposition: str
    side_effect_receipt_refs: list[str]


class RecallStagePatch(TypedDict, total=False):
    recall_cache: dict[str, str]


class ContextBuildStagePatch(TypedDict):
    context_pack_ref: str
    model_request_ref: str
    context_operation: str
    context_compaction_mode: str


class ModelStagePatch(TypedDict, total=False):
    model_response_ref: str


class ResultProcessStagePatch(TypedDict, total=False):
    action_type: str
    action_hash: str
    agent_action_ref: str
    tool_action_refs: list[str]
    tool_call_count: int
    tool_observation_refs: list[str]
    side_effect_receipt_refs: list[str]
    reason_codes: list[str]
    pass_disposition: str


class EndStagePatch(TypedDict, total=False):
    pass_disposition: str
    iteration: int
    tool_observation_refs: list[str]
    final_output_ref: str


PipelineStagePatch = (
    PreRecallStagePatch
    | RecallStagePatch
    | ContextBuildStagePatch
    | ModelStagePatch
    | ResultProcessStagePatch
    | EndStagePatch
)


class _StageData(BaseModel):
    model_config = ConfigDict(frozen=True)


class RuleExecutionError(_StageData):
    """Safe error information returned by a Rule."""

    reason_code: str
    message: str = ""


class PreRecallRuleInput(_StageData):
    request_ref: str = ""
    recall_plan_ref: str = ""
    iteration: int = 0


class PreRecallRuleOutput(_StageData):
    recall_plan_ref: str | None = None
    context_compaction_mode: Literal["auto", "force"] | None = None
    side_effect_receipt_refs: tuple[str, ...] = ()


class RecallRuleInput(_StageData):
    request_ref: str = ""
    recall_plan_ref: str = ""
    recall_cache: dict[str, str] = Field(default_factory=dict)


class RecallRuleOutput(_StageData):
    recall_cache: dict[str, str] | None = None


class ModelRuleInput(_StageData):
    agent_id: str = ""
    context_pack_ref: str = ""
    model_request_ref: str = ""
    iteration: int = 0
    model_response_ref: str = ""


class ModelRuleOutput(_StageData):
    model_response_ref: str


class ResultProcessRuleInput(_StageData):
    model_response_ref: str = ""
    context_pack_ref: str = ""
    context_operation: str = "normal"
    iteration: int = 0
    action_type: str = ""
    action_hash: str = ""
    agent_action_ref: str = ""
    tool_action_refs: tuple[str, ...] = ()


class ResultProcessRuleOutput(_StageData):
    """Rules react to the already-formed action; every field is optional."""

    tool_observation_refs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    side_effect_receipt_refs: tuple[str, ...] = ()
    # A rule may terminate the pass early (e.g. a human rejected approval);
    # the End router then preserves this disposition unchanged.
    pass_disposition: Literal["next_pass", "run_completed", "run_failed"] | None = None


class EndRuleInput(_StageData):
    action_type: str = ""
    action_hash: str = ""
    agent_action_ref: str = ""
    iteration: int = 0
    pass_disposition: str = ""
    tool_observation_refs: tuple[str, ...] = ()
    final_output_ref: str = ""


class EndRuleOutput(_StageData):
    pass_disposition: Literal["next_pass", "run_completed", "run_failed"]
    iteration: int
    tool_observation_refs: tuple[str, ...] = ()
    final_output_ref: str | None = None
