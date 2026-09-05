"""Low-level model transport selection hidden behind the ModelAdapter port."""
from __future__ import annotations

import json
from typing import Protocol

from usagi_agent.agents.model_adapters import (
    OpenAICompatibleModelAdapter,
    ResponsesModelAdapter,
)
from usagi_agent.ports import ModelAdapter
from usagi_agent.types.model import ModelSpec

ModelAdapterCacheKey = tuple[str, str, str, str | None, str, bool, str]


class ManagedModelAdapter(ModelAdapter, Protocol):
    """Model adapter created and owned by the infrastructure layer."""

    async def shutdown(self) -> None: ...


def model_adapter_cache_key(spec: ModelSpec) -> ModelAdapterCacheKey:
    """Identify a concrete deployment, including its wire protocol."""
    return (
        spec.id,
        spec.protocol,
        spec.provider_model,
        spec.base_url,
        spec.api_key_env,
        spec.store_provider_response,
        json.dumps(spec.extra_body, sort_keys=True, separators=(",", ":")),
    )


def create_model_adapter(spec: ModelSpec) -> ManagedModelAdapter:
    """Create the transport selected by infrastructure model configuration."""
    if spec.protocol == "openai_responses":
        return ResponsesModelAdapter(spec)
    if spec.protocol == "openai_chat":
        return OpenAICompatibleModelAdapter(spec)
    raise ValueError(f"unsupported model protocol: {spec.protocol}")


__all__ = [
    "ModelAdapterCacheKey",
    "ManagedModelAdapter",
    "create_model_adapter",
    "model_adapter_cache_key",
]
