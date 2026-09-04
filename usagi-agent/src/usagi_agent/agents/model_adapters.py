"""Concrete model adapters.

Adapters translate provider SDK objects into the serializable, provider-neutral
``ModelResponse`` wire contract. They deliberately do not parse structured
output, tool arguments, or form AgentActions; ResultProcess owns those tasks.
"""
from __future__ import annotations

import json
import os
from typing import Any

from usagi_agent.ports import HealthStatus, ToolContext
from usagi_agent.types.model import (
    ModelRequest,
    ModelResponse,
    ModelSpec,
    ModelToolCall,
    ModelUsage,
)


class ScriptedModelAdapter:
    """Deterministic offline model used only for scripted execution."""

    adapter_ref = "usagi.scripted_model"

    def __init__(self) -> None:
        self._tool_used_by_run: set[str] = set()

    async def generate(
        self, request: ModelRequest, ctx: ToolContext
    ) -> ModelResponse:
        run_id = ctx.execution.control_id
        if request.prompt_ref == "usagi.context_compaction_prompt@1.1.0":
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
                content=json.dumps(
                    {
                        "answer": "",
                        "context_update": {
                            "compacted": True,
                            "summary": summary,
                            "facts": previous.get("facts", {}),
                            "constraints": previous.get("constraints", []),
                            "goals": previous.get("goals", []),
                            "open_tasks": previous.get("open_tasks", []),
                            "artifacts": previous.get("artifacts", []),
                        },
                        "long_term_memory_candidates": [
                            {
                                "content": (
                                    f"Durable information extracted from {event_count} "
                                    "earlier events."
                                ),
                                "type": "semantic",
                                "confidence": 0.8,
                                "importance": 0.6,
                            }
                        ] if events else [],
                    }
                ),
                finish_reason="stop",
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
                tool_calls=[
                    ModelToolCall(
                        tool_name=tool_name,
                        tool_call_id=f"scripted_{run_id}",
                        raw_arguments=json.dumps(arguments),
                    )
                ],
                finish_reason="tool_calls",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        return ModelResponse(
            content="scripted response",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    async def health(self) -> HealthStatus:
        return "healthy"


class OpenAICompatibleModelAdapter:
    """OpenAI-compatible transport adapter with no action interpretation."""

    adapter_ref = "usagi.openai_compatible"

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI  # pyright: ignore[reportMissingImports]

        api_key = os.environ.get(self.spec.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"missing model credential environment variable: {self.spec.api_key_env}"
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=self.spec.base_url)
        return self._client

    async def generate(
        self, request: ModelRequest, ctx: ToolContext
    ) -> ModelResponse:
        messages: list[dict[str, object]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(request.messages)
        kwargs: dict[str, object] = {
            "model": self.spec.provider_model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            kwargs.update(
                tools=request.tools,
                tool_choice="auto",
                parallel_tool_calls=True,
            )
        if request.structured_output:
            kwargs["response_format"] = {"type": "json_object"}
        completion = await self._get_client().chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message
        usage = completion.usage
        return ModelResponse(
            content=message.content,
            tool_calls=[
                ModelToolCall(
                    tool_name=call.function.name,
                    tool_call_id=call.id,
                    raw_arguments=call.function.arguments or "{}",
                )
                for call in message.tool_calls or []
            ],
            finish_reason=choice.finish_reason,
            usage=ModelUsage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            ),
        )

    async def health(self) -> HealthStatus:
        return "healthy" if os.environ.get(self.spec.api_key_env) else "degraded"

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.close()
