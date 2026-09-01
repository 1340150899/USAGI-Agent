from decimal import Decimal

from examples.structured_agent.rules import (
    ResearchWriterContextBuildConfig,
    ResearchWriterEndConfig,
    ResearchWriterModelConfig,
    ResearchWriterPreRecallConfig,
    ResearchWriterRecallConfig,
    ResearchWriterResultProcessConfig,
)
from usagi_agent.pipelines import AgentPipelineConfig
from usagi_agent.pipelines.rules import StageType
from usagi_agent.scenarios import ScenarioConfig
from usagi_agent.types.budget import Budget

RESEARCH_WRITER_SCENARIO = ScenarioConfig(
    key="example.research_writer",
    agent_id="research_writer",
    pipeline=AgentPipelineConfig(
        pre_recall=ResearchWriterPreRecallConfig(
            name="research_writer_pre_recall", type=StageType.PRE_RECALL
        ),
        recall=ResearchWriterRecallConfig(
            name="research_writer_recall", type=StageType.RECALL
        ),
        context_build=ResearchWriterContextBuildConfig(
            name="research_writer_context_build", type=StageType.CONTEXT_BUILD
        ),
        model=ResearchWriterModelConfig(
            name="research_writer_model", type=StageType.MODEL
        ),
        result_process=ResearchWriterResultProcessConfig(
            name="research_writer_result_process", type=StageType.RESULT_PROCESS
        ),
        end=ResearchWriterEndConfig(name="research_writer_end", type=StageType.END),
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
