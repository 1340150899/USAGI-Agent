"""Model request/response contract (design §18)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from usagi_agent.types.action import ToolAction
from usagi_agent.types.refs import ArtifactRef


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")


class ModelRequest(BaseModel):
    model_id: str
    prompt_ref: str = ""
    system_prompt: str = ""
    messages_ref: ArtifactRef
    context_pack_ref: ArtifactRef
    tools: list[dict[str, object]] = Field(default_factory=list)
    max_output_tokens: int | None = None
    temperature: float = 0.0


class ModelResponse(BaseModel):
    content_ref: ArtifactRef
    tool_calls: list[ToolAction] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_use", "length", "error"] = "stop"
    usage: ModelUsage = Field(default_factory=ModelUsage)
