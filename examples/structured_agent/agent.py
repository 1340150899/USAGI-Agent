from usagi_agent.agents import AgentSpec
from usagi_agent.models import DEFAULT_MODEL
from usagi_agent.server import Server

RESEARCH_WRITER_AGENT_ID = "research_writer"


def create_research_writer_agent(server: Server) -> AgentSpec:
    """Register the example Agent through the public server facade."""

    return server.create_agent(
        id=RESEARCH_WRITER_AGENT_ID,
        input_schema="usagi.agent_request@1.0.0",
        model=DEFAULT_MODEL,
        allowed_tools=("web_search", "current_time"),
    )
