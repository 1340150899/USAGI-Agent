"""Own Agent runtimes and execute fully prepared model requests."""
from __future__ import annotations

import inspect
import os
from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from usagi_agent.agents.model_adapter_factory import (
    ModelAdapterCacheKey,
    create_model_adapter,
    model_adapter_cache_key,
)
from usagi_agent.agents.model_adapters import ScriptedModelAdapter
from usagi_agent.agents.spec import AgentSpec
from usagi_agent.agents.schemas import OutputContract, OutputSchemaManager
from usagi_agent.api.errors import DuplicateAgentError, UnknownAgentError
from usagi_agent.ports import HealthStatus, ModelAdapter, ToolContext
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelSpec, ModelUsage
from usagi_agent.types.refs import SchemaRef


class AgentManager:
    def __init__(
        self,
        model_execution_mode: Literal["live", "scripted"] = "live",
    ) -> None:
        self._agents: dict[str, AgentSpec] = {}
        self._model_adapter: ModelAdapter | None = (
            ScriptedModelAdapter() if model_execution_mode == "scripted" else None
        )
        self._live_adapters: dict[ModelAdapterCacheKey, ModelAdapter] = {}
        self._usage_by_agent: dict[str, ModelUsage] = defaultdict(ModelUsage)
        self._output_schemas = OutputSchemaManager()

    def compile_output_schema(
        self,
        ref: SchemaRef,
        *,
        name: str,
        schema: Mapping[str, object],
        max_retries: int = 5,
    ) -> OutputContract:
        """Compile and register an Agent-owned output schema."""
        return self._output_schemas.register(
            ref, name=name, schema=schema, max_retries=max_retries
        )

    def resolve_output_schema(self, ref: SchemaRef) -> OutputContract:
        return self._output_schemas.resolve(ref)

    def register(self, spec: AgentSpec) -> AgentSpec:
        """Register an already assembled Agent declaration."""
        if spec.output_schema is not None:
            self.resolve_output_schema(spec.output_schema)
        if spec.id in self._agents:
            raise DuplicateAgentError(spec.id)
        self._agents[spec.id] = spec
        return spec

    def output_contract(self, agent_id: str) -> OutputContract | None:
        ref = self.get(agent_id).output_schema
        return self._output_schemas.resolve(ref) if ref is not None else None

    def get(self, agent_id: str) -> AgentSpec:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise UnknownAgentError(agent_id) from exc

    def all_agents(self) -> tuple[AgentSpec, ...]:
        return tuple(self._agents.values())

    def set_model_adapter(self, adapter: ModelAdapter | None) -> None:
        """Inject a model boundary implementation, including deterministic test mocks."""
        self._model_adapter = adapter

    async def generate(
        self, *, agent_id: str, request: ModelRequest, context: ToolContext
    ) -> ModelResponse:
        agent = self.get(agent_id)
        spec = agent.model
        override = self._model_adapter
        response = (
            await override.generate(request, context)
            if override is not None
            else await self._live_adapter_for(spec).generate(
                request, context
            )
        )
        priced = response.usage.model_copy(
            update={
                "cost": (
                    Decimal(response.usage.input_tokens) * spec.input_cost_per_million
                    + Decimal(response.usage.output_tokens) * spec.output_cost_per_million
                ) / Decimal(1_000_000)
            }
        )
        response = response.model_copy(update={"usage": priced})
        self._record_usage(agent_id, priced)
        return response

    def _live_adapter_for(self, spec: ModelSpec) -> ModelAdapter:
        key = model_adapter_cache_key(spec)
        adapter = self._live_adapters.get(key)
        if adapter is None:
            adapter = create_model_adapter(spec)
            self._live_adapters[key] = adapter
        return adapter

    def _record_usage(self, agent_id: str, usage: ModelUsage) -> None:
        bucket = self._usage_by_agent[agent_id]
        bucket.input_tokens += usage.input_tokens
        bucket.output_tokens += usage.output_tokens
        bucket.cost += usage.cost

    def usage_for_agent(self, agent_id: str) -> ModelUsage:
        self.get(agent_id)
        return self._usage_by_agent[agent_id].model_copy(deep=True)

    async def health(self) -> HealthStatus:
        statuses = [
            await adapter.health()
            for adapter in self._runtime_adapters()
        ]
        native_missing_key = any(
            self._model_adapter is None
            and not os.environ.get(agent.model.api_key_env)
            for agent in self._agents.values()
        )
        if "unhealthy" in statuses:
            return "unhealthy"
        if "degraded" in statuses or native_missing_key:
            return "degraded"
        return "healthy"

    async def shutdown(self) -> None:
        resources = list(self._runtime_adapters())
        seen: set[int] = set()
        for resource in resources:
            if id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "shutdown", None) or getattr(resource, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    def _runtime_adapters(self) -> tuple[ModelAdapter, ...]:
        override = (self._model_adapter,) if self._model_adapter is not None else ()
        return (*override, *self._live_adapters.values())
