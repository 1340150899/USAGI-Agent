"""Model request/response contract (design §18)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.action import ToolAction
from usagi_agent.types.refs import ArtifactRef
from usagi_agent.types.context import ContextUpdate


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")


class ModelSpec(BaseModel):
    """Immutable source information for one model deployment."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider_model: str
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    context_window: int = 128_000
    default_max_output_tokens: int = 4_096
    input_cost_per_million: Decimal = Decimal("0")
    output_cost_per_million: Decimal = Decimal("0")


class ModelRequest(BaseModel):
    prompt_ref: str = ""
    system_prompt: str = ""
    messages_ref: ArtifactRef
    context_pack_ref: ArtifactRef
    tools: list[dict[str, object]] = Field(default_factory=list)
    messages: list[dict[str, object]] = Field(default_factory=list)
    max_output_tokens: int
    temperature: float = 0.0
    structured_output: bool = True


class ModelResponse(BaseModel):
    content_ref: ArtifactRef
    tool_calls: list[ToolAction] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_use", "length", "error"] = "stop"
    usage: ModelUsage = Field(default_factory=ModelUsage)
    content: str | None = None
    context_update: ContextUpdate | None = None
