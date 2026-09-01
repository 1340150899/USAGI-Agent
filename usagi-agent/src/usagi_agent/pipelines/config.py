from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.pipelines.rules import (
    ContextBuildAdapterConfig,
    EndAdapterConfig,
    ModelRuleAdapterConfig,
    PreRecallAdapterConfig,
    RecallAdapterConfig,
    ResultProcessAdapterConfig,
)
from usagi_agent.types.budget import Budget


class AgentPipelineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    pre_recall: PreRecallAdapterConfig
    recall: RecallAdapterConfig
    context_build: ContextBuildAdapterConfig
    model: ModelRuleAdapterConfig
    result_process: ResultProcessAdapterConfig
    end: EndAdapterConfig
    max_passes: int = Field(default=5, ge=1)
    max_tool_calls: int = Field(default=10, ge=1)
    max_delegations: int = Field(default=0, ge=0)
    budget: Budget = Field(default_factory=Budget)
