"""Stage-specific Rule inputs and outputs, independent of LangGraph state."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StatePatch = dict[str, object]


class _StageData(BaseModel):
    model_config = ConfigDict(frozen=True)


class RuleExecutionError(_StageData):
    """Safe error information returned by a Rule."""

    reason_code: str
    message: str = ""


class PreRecallRuleInput(_StageData):
    request_ref: str = ""
    normalized_input_ref: str = ""
    recall_plan_ref: str = ""


class PreRecallRuleOutput(_StageData):
    normalized_input_ref: str | None = None
    recall_plan_ref: str | None = None


class RecallRuleInput(_StageData):
    normalized_input_ref: str = ""
    recall_plan_ref: str = ""
    recall_cache: dict[str, str] = Field(default_factory=dict)
    recall_bundle_ref: str = ""


class RecallRuleOutput(_StageData):
    recall_cache: dict[str, str] | None = None
    recall_bundle_ref: str | None = None


class ModelRuleInput(_StageData):
    context_pack_ref: str = ""
    model_request_ref: str = ""
    iteration: int = 0
    model_response_ref: str = ""


class ModelRuleOutput(_StageData):
    model_response_ref: str


class ResultProcessRuleInput(_StageData):
    model_response_ref: str = ""
    iteration: int = 0
    action_type: str = ""
    action_hash: str = ""
    agent_action_ref: str = ""


class ResultProcessRuleOutput(_StageData):
    action_type: str
    action_hash: str
    agent_action_ref: str


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
