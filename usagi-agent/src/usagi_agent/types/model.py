"""Model request/response contract (design §18)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.refs import ArtifactRef

ModelInputModality = Literal["text", "image"]
ModelProtocol = Literal["openai_chat", "openai_responses"]


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")


class ModelSpec(BaseModel):
    """Immutable source information for one model deployment."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider_model: str
    protocol: ModelProtocol = "openai_chat"
    input_modalities: frozenset[ModelInputModality] = frozenset({"text"})
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    context_window: int = 128_000
    default_max_output_tokens: int = 4_096
    input_cost_per_million: Decimal = Decimal("0")
    output_cost_per_million: Decimal = Decimal("0")
    store_provider_response: bool = Field(
        default=False,
        description="Allow the provider to retain generated response objects.",
    )
    extra_body: dict[str, object] = Field(
        default_factory=dict,
        description="Provider-specific OpenAI-compatible request extensions.",
    )


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


class ModelToolCall(BaseModel):
    """Provider-neutral, unparsed tool call returned by a model adapter."""

    tool_name: str
    tool_call_id: str
    raw_arguments: str = "{}"


class ModelResponse(BaseModel):
    """Serializable raw model result; interpretation belongs to ResultProcess."""

    content_ref: ArtifactRef | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    content: str | None = None
