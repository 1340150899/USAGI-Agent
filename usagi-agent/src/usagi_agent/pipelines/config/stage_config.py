"""Default stage configuration assembled from framework-owned rules."""
from decimal import Decimal

from usagi_agent.pipelines import AgentPipelineConfig
from usagi_agent.pipelines.rules import (
    CompactionApplyRule,
    LongTermMemoryRecallRule,
    ToolExecutionRule,
    ToolObservationRecallRule,
)
from usagi_agent.scenarios import ScenarioConfig
from usagi_agent.types.budget import Budget


RESEARCH_WRITER_SCENARIO = ScenarioConfig(
    key="example.research_writer",
    agent_id="research_writer",
    pipeline=AgentPipelineConfig(
        recall=(
            LongTermMemoryRecallRule(),
            ToolObservationRecallRule(),
        ),
        result_process=(ToolExecutionRule(), CompactionApplyRule()),
        max_passes=5,
        max_tool_calls=10,
        max_delegations=0,
        budget=Budget(
            max_total_cost=Decimal("1.0"),
            max_input_tokens=100000,
            max_output_tokens=20000,
        ),
    ),
)

SCENARIO_CONFIGS = (RESEARCH_WRITER_SCENARIO,)

__all__ = ["RESEARCH_WRITER_SCENARIO", "SCENARIO_CONFIGS"]
