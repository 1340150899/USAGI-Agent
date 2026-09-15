"""Static Agent declaration that directly owns its model and prompt choices."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.model import ModelSpec
from usagi_agent.types.refs import SchemaRef


class AgentSpec(BaseModel):
    """Immutable Agent declaration with a tool-name allowlist."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    input_schema: SchemaRef
    output_schema: SchemaRef | None = None
    model: ModelSpec
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
