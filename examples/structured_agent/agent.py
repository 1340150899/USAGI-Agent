from usagi_agent.agents import AgentManager, AgentSpec
from usagi_agent.models import GLM_5_2_MODEL

RESEARCH_WRITER_AGENT_ID = "research_writer"


def create_research_writer_agent(manager: AgentManager) -> AgentSpec:
    """Ask AgentManager to construct and register the example AgentSpec."""

    return manager.create_agent(
        id=RESEARCH_WRITER_AGENT_ID,
        input_schema="usagi.agent_request@1.0.0",
        output_schema="usagi.final_output@1.0.0",
        model=GLM_5_2_MODEL,
        allowed_tools=("web_search", "current_time"),
    )
