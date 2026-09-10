"""Static prompt declarations consumed while building model context."""

from usagi_agent.prompts.catalog import (
    CONTEXT_COMPACTION_PROMPT,
    PROMPTS_BY_AGENT_ID,
    RESEARCH_WRITER_PROMPT,
    WECHAT_MATERIAL_PROMPT,
    prompt_for_agent,
)
from usagi_agent.prompts.spec import PromptSpec

__all__ = [
    "CONTEXT_COMPACTION_PROMPT",
    "PROMPTS_BY_AGENT_ID",
    "RESEARCH_WRITER_PROMPT",
    "WECHAT_MATERIAL_PROMPT",
    "PromptSpec",
    "prompt_for_agent",
]
