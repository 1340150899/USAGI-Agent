from usagi_agent.agents import AgentSpec
from usagi_agent.types.refs import PromptRef

RESEARCH_WRITER_AGENT = AgentSpec(
    id="research_writer",
    input_schema="usagi.agent_request@1.0.0",
    output_schema="usagi.final_output@1.0.0",
    prompt=PromptRef("usagi.research_writer_prompt@1.0.0"),
    prompt_template="You are a concise research writer.",
    allowed_tools=("web_search", "current_time"),
)
