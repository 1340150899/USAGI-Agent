"""AgentSpec (design §13.1).

v1 Agent is a static, typed reasoning definition — no mutable state, no per-Agent model/
timeout/token/fallback config (§13.1). Differences are expressed as different Agents or
explicit Adapters, not layered config overrides.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.refs import PromptRef, SchemaRef


class AgentSpec(BaseModel):
    """Immutable Agent declaration with a tool-name allowlist."""

    model_config = ConfigDict(frozen=True)

    id: str
    input_schema: SchemaRef
    output_schema: SchemaRef
    prompt: PromptRef
    prompt_template: str = ""
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
