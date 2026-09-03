"""Own Agent runtimes and execute fully prepared model requests."""
from __future__ import annotations

import inspect
import json
import os
from collections import defaultdict
from decimal import Decimal
from typing import Any, Literal

from usagi_agent.agents.spec import AgentSpec
from usagi_agent.api.errors import DuplicateAgentError, UnknownAgentError
from usagi_agent.kernel.context import RunContext
from usagi_agent.ports import HealthStatus, ModelAdapter, ToolContext
from usagi_agent.types.action import ToolAction
from usagi_agent.types.context import ContextUpdate
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelSpec, ModelUsage
from usagi_agent.types.refs import ArtifactRef, SchemaRef


class ScriptedModelAdapter:
    """Deterministic offline model used only when scripted execution is enabled."""

    adapter_ref = "usagi.scripted_model"

    def __init__(self) -> None:
        self._tool_used_by_run: set[str] = set()

    async def generate(
        self, request: ModelRequest, ctx: ToolContext
    ) -> ModelResponse:
        run_id = ctx.execution.control_id
        if request.prompt_ref == "usagi.context_compaction_prompt@1.0.0":
            payload = json.loads(str(request.messages[-1].get("content", "{}")))
            previous = payload.get("previous_context", {})
            events = payload.get("events_to_compact", [])
            event_count = len(events)
            previous_summary = str(previous.get("summary", "")).strip()
            summary = (
                f"{previous_summary}\nCompacted {event_count} earlier events."
                if previous_summary
                else f"Compacted {event_count} earlier events."
            )
            return ModelResponse(
                content_ref=ArtifactRef(
                    artifact_id="unpersisted:scripted-compaction",
                    content_type="text/plain",
                ),
                content="",
                context_update=ContextUpdate(
                    compacted=True,
                    summary=summary,
                    facts=previous.get("facts", {}),
                    constraints=previous.get("constraints", []),
                    goals=previous.get("goals", []),
                    open_tasks=previous.get("open_tasks", []),
                    artifacts=previous.get("artifacts", []),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        if request.tools and run_id not in self._tool_used_by_run:
            self._tool_used_by_run.add(run_id)
            function = request.tools[0].get("function", {})
            if not isinstance(function, dict):
                function = {}
            tool_name = str(function.get("name", "scripted_tool"))
            parameters = function.get("parameters", {})
            required = parameters.get("required", []) if isinstance(parameters, dict) else []
            latest_user = next(
                (
                    str(message.get("content", ""))
                    for message in reversed(request.messages)
                    if message.get("role") == "user"
                ),
                "scripted input",
            )
            arguments: dict[str, object] = {
                str(name): latest_user if name == "query" else f"scripted-{name}"
                for name in required
            }
            return ModelResponse(
                content_ref=ArtifactRef(
                    artifact_id="unpersisted:scripted-tool",
                    content_type="text/plain",
                ),
                tool_calls=[
                    ToolAction(
                        tool_name=tool_name,
                        tool_call_id=f"scripted_{run_id}",
                        arguments=arguments,
                    )
                ],
                finish_reason="tool_use",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        return ModelResponse(
            content_ref=ArtifactRef(
                artifact_id="unpersisted:scripted-final", content_type="text/plain"
            ),
            content="scripted response",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    async def health(self) -> HealthStatus:
        return "healthy"


class AgentManager:
    def __init__(
        self, model_execution_mode: Literal["live", "scripted"] = "live"
    ) -> None:
        self._agents: dict[str, AgentSpec] = {}
        self._model_adapter: ModelAdapter | None = (
            ScriptedModelAdapter() if model_execution_mode == "scripted" else None
        )
        self._clients: dict[str, Any] = {}
        self._usage_by_agent: dict[str, ModelUsage] = defaultdict(ModelUsage)

    def create_agent(
        self,
        *,
        id: str,
        input_schema: SchemaRef,
        output_schema: SchemaRef,
        model: ModelSpec,
        allowed_tools: tuple[str, ...] = (),
    ) -> AgentSpec:
        spec = AgentSpec(
            id=id,
            input_schema=input_schema,
            output_schema=output_schema,
            model=model,
            allowed_tools=allowed_tools,
        )
        if spec.id in self._agents:
            raise DuplicateAgentError(spec.id)
        self._agents[spec.id] = spec
        return spec

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
        self, *, agent_id: str, request: ModelRequest, context: RunContext
    ) -> ModelResponse:
        agent = self.get(agent_id)
        spec = agent.model
        override = self._model_adapter
        response = (
            await override.generate(request, context.to_tool_context())
            if override is not None
            else await self._generate_openai_compatible(spec, request)
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

    def _client_for(self, spec: ModelSpec):
        client = self._clients.get(spec.id)
        if client is not None:
            return client
        from openai import AsyncOpenAI  # pyright: ignore[reportMissingImports]

        api_key = os.environ.get(spec.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"missing model credential environment variable: {spec.api_key_env}"
            )
        client = AsyncOpenAI(api_key=api_key, base_url=spec.base_url)
        self._clients[spec.id] = client
        return client

    async def _generate_openai_compatible(
        self, spec: ModelSpec, request: ModelRequest
    ) -> ModelResponse:
        messages: list[dict[str, object]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(request.messages)
        kwargs: dict[str, object] = {
            "model": spec.provider_model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            kwargs.update(
                tools=request.tools,
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        if request.structured_output:
            kwargs["response_format"] = {"type": "json_object"}
        # Provider-compatible endpoints accept a dynamic subset of Chat Completions
        # parameters, so keep this boundary intentionally adapter-typed.
        client: Any = self._client_for(spec)
        completion = await client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message
        tool_calls: list[ToolAction] = []
        # AgentLoop executes one action per pass, so retain exactly one model call.
        for call in (message.tool_calls or [])[:1]:
            try:
                arguments = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                arguments = {"raw": call.function.arguments}
            tool_calls.append(
                ToolAction(
                    tool_name=call.function.name,
                    tool_call_id=call.id,
                    arguments=arguments,
                )
            )
        content = message.content or ""
        context_update = None
        if request.structured_output and content:
            try:
                structured = json.loads(content)
                if isinstance(structured, dict) and "answer" in structured:
                    content = str(structured["answer"])
                    update = structured.get("context_update")
                    if isinstance(update, dict):
                        context_update = ContextUpdate.model_validate(update)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        usage = completion.usage
        return ModelResponse(
            content_ref=ArtifactRef(
                artifact_id=f"unpersisted:{completion.id}", content_type="text/plain"
            ),
            content=content,
            context_update=context_update,
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else (
                "length" if choice.finish_reason == "length" else "stop"
            ),
            usage=ModelUsage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            ),
        )

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
        resources = [*self._runtime_adapters(), *self._clients.values()]
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
        return (self._model_adapter,) if self._model_adapter is not None else ()
