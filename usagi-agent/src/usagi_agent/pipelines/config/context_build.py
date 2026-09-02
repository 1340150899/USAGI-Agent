"""Configuration for the internal Context Build pipeline."""
from pydantic import BaseModel, ConfigDict

from usagi_agent.pipelines.rules.context_build import (
    ContextFilterAdapterConfig,
    ContextRankAdapterConfig,
)


class ContextBuildPipelineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    filters: tuple[ContextFilterAdapterConfig, ...] = ()
    rankers: tuple[ContextRankAdapterConfig, ...] = ()
