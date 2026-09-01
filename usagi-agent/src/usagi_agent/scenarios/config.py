from pydantic import BaseModel, ConfigDict

from usagi_agent.pipelines.config import AgentPipelineConfig


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    key: str
    agent_id: str
    pipeline: AgentPipelineConfig
