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
from usagi_agent.types.content import ImageContentPart


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

    async def _messages_for_openai(
        self, messages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for message in messages:
            copied = dict(message)
            content = copied.get("content")
            if isinstance(content, list):
                copied["content"] = [
                    await self._content_part_for_openai(part)
                    for part in content
                    if isinstance(part, dict)
                ]
            normalized.append(copied)
        return normalized

    async def _content_part_for_openai(
        self, part: dict[str, object]
    ) -> dict[str, object]:
        if part.get("type") == "text":
            return {"type": "text", "text": str(part.get("text", ""))}
        if part.get("type") != "image":
            return dict(part)

        image = ImageContentPart.model_validate(part)
        image_url = image.url
        if image.artifact_ref is not None:
            raise RuntimeError(
                "unmaterialized image ArtifactRef reached the provider adapter"
            )
        assert image_url is not None
        return {
            "type": "image_url",
            "image_url": {"url": image_url, "detail": image.detail},
        }

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
        messages.extend(await self._messages_for_openai(request.messages))
        kwargs: dict[str, object] = {
            "model": self.spec.provider_model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if self.spec.extra_body:
            kwargs["extra_body"] = self.spec.extra_body
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


class ResponsesModelAdapter:
    """OpenAI Responses-protocol transport adapter with no action interpretation.

    Speaks the stateless ``responses.create`` wire format (input replay, no
    ``previous_response_id``) so USAGI keeps owning context assembly.
    """

    adapter_ref = "usagi.openai_responses"

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self._client: Any | None = None

    async def _input_for_responses(
        self, messages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for message in messages:
            items.extend(await self._items_for_message(message))
        return items

    async def _items_for_message(
        self, message: dict[str, object]
    ) -> list[dict[str, object]]:
        role = str(message.get("role", "user"))
        content = message.get("content")
        parts = (
            [part for part in content if isinstance(part, dict)]
            if isinstance(content, list)
            else None
        )
        if role == "tool":
            wire_output: object
            if parts is None:
                wire_output = content if isinstance(content, str) else ""
            else:
                wire_output = [
                    await self._content_part_for_responses(part)
                    for part in parts
                ]
            return [
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id", "")),
                    "output": wire_output,
                }
            ]
        if role == "assistant":
            return self._assistant_items(message, content)
        wire_content: object
        if parts is None:
            wire_content = content if isinstance(content, str) else ""
        else:
            wire_content = [await self._content_part_for_responses(p) for p in parts]
        return [{"role": role, "content": wire_content}]

    def _assistant_items(
        self, message: dict[str, object], content: object
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        text = content if isinstance(content, str) else None
        if text:
            items.append({"role": "assistant", "content": text})
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function", {})
                if not isinstance(function, dict):
                    function = {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id", "")),
                        "name": str(function.get("name", "")),
                        "arguments": str(function.get("arguments", "{}")) or "{}",
                    }
                )
        return items

    async def _content_part_for_responses(
        self, part: dict[str, object]
    ) -> dict[str, object]:
        if part.get("type") == "text":
            return {"type": "input_text", "text": str(part.get("text", ""))}
        if part.get("type") != "image":
            return dict(part)
        image = ImageContentPart.model_validate(part)
        image_url = image.url
        if image.artifact_ref is not None:
            raise RuntimeError(
                "unmaterialized image ArtifactRef reached the provider adapter"
            )
        assert image_url is not None
        return {
            "type": "input_image",
            "image_url": image_url,
            "detail": image.detail,
        }

    @staticmethod
    def _tools_for_responses(
        tools: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        wire: list[dict[str, object]] = []
        for tool in tools:
            function = tool.get("function")
            if isinstance(function, dict):
                flattened = dict(tool)
                flattened.pop("function", None)
                flattened.update(function)
                wire.append(flattened)
            else:
                wire.append(dict(tool))
        return wire

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
        kwargs: dict[str, object] = {
            "model": self.spec.provider_model,
            "input": await self._input_for_responses(request.messages),
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "store": self.spec.store_provider_response,
        }
        if request.system_prompt:
            kwargs["instructions"] = request.system_prompt
        if self.spec.extra_body:
            kwargs["extra_body"] = self.spec.extra_body
        if request.tools:
            kwargs.update(
                tools=self._tools_for_responses(request.tools),
                tool_choice="auto",
                parallel_tool_calls=True,
            )
        if request.structured_output:
            kwargs["text"] = {"format": {"type": "json_object"}}
        response = await self._get_client().responses.create(**kwargs)

        status = str(getattr(response, "status", ""))
        error = getattr(response, "error", None)
        if error is not None or status == "failed":
            error_code = str(getattr(error, "code", "provider_error"))
            raise RuntimeError(f"responses provider failed: {error_code}")
        if status in {"queued", "in_progress"}:
            raise RuntimeError(
                f"responses.create returned non-terminal status: {status}"
            )

        output_text = getattr(response, "output_text", None)
        texts: list[str] = [output_text] if isinstance(output_text, str) else []
        tool_calls: list[ModelToolCall] = []
        for item in getattr(response, "output", None) or []:
            item_type = str(getattr(item, "type", ""))
            if item_type == "message" and not texts:
                for part in getattr(item, "content", None) or []:
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        texts.append(text)
            elif item_type == "function_call":
                tool_calls.append(
                    ModelToolCall(
                        tool_name=str(getattr(item, "name", "")),
                        tool_call_id=str(getattr(item, "call_id", "")),
                        raw_arguments=str(getattr(item, "arguments", "") or "{}"),
                    )
                )
        usage = getattr(response, "usage", None)
        finish_reason = self._finish_reason(response, status, bool(tool_calls))
        return ModelResponse(
            content="\n".join(texts) if texts else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=ModelUsage(
                input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            ),
        )

    @staticmethod
    def _finish_reason(response: object, status: str, has_tool_calls: bool) -> str:
        if has_tool_calls:
            return "tool_calls"
        if status == "completed":
            return "stop"
        if status == "cancelled":
            return "cancelled"
        if status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = str(getattr(details, "reason", "incomplete"))
            return "length" if reason == "max_output_tokens" else reason
        raise RuntimeError(f"responses provider returned unknown status: {status}")

    async def health(self) -> HealthStatus:
        return "healthy" if os.environ.get(self.spec.api_key_env) else "degraded"

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.close()
