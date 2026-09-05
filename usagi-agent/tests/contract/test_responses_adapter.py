"""Contract tests for the OpenAI Responses wire protocol adapter."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from usagi_agent.agents.manager import AgentManager
from usagi_agent.agents.model_adapter_factory import create_model_adapter
from usagi_agent.agents.model_adapters import (
    OpenAICompatibleModelAdapter,
    ResponsesModelAdapter,
)
from usagi_agent.types.model import ModelRequest, ModelSpec
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.types.refs import ArtifactRef, PrincipalRef

RESPONSES_SPEC = ModelSpec(
    id="vision:glm-5.3-flash",
    provider_model="glm-5.3-flash",
    protocol="openai_responses",
    input_modalities=frozenset({"text", "image"}),
    base_url="https://open.bigmodel.cn/api/v1",
    api_key_env="GLM_API_KEY",
    context_window=128_000,
    default_max_output_tokens=8_192,
)


def _context() -> ToolContext:
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id="tenant",
            principal=PrincipalRef(
                principal_kind="system", principal_opaque_id="test"
            ),
            authorization_scope=(),
            control_kind="run",
            control_id="run_test",
            fencing_token=1,
        )
    )


@pytest.mark.asyncio
async def test_chat_history_serializes_to_responses_input_items():
    adapter = ResponsesModelAdapter(RESPONSES_SPEC)
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "find the bottle"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "locate_object",
                        "arguments": '{"target": "bottle"}',
                    },
                }
            ],
        },
        {"role": "tool", "content": '{"x": 12}', "tool_call_id": "call_1"},
        {"role": "assistant", "content": "found it"},
    ]
    items = await adapter._input_for_responses(messages)
    assert items == [
        {"role": "user", "content": "find the bottle"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "locate_object",
            "arguments": '{"target": "bottle"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"x": 12}',
        },
        {"role": "assistant", "content": "found it"},
    ]


@pytest.mark.asyncio
async def test_multimodal_parts_map_to_responses_content_types():
    adapter = ResponsesModelAdapter(RESPONSES_SPEC)
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {
                    "type": "image",
                    "media_type": "image/jpeg",
                    "artifact_ref": None,
                    "url": "data:image/jpeg;base64,aGk=",
                    "detail": "high",
                },
            ],
        }
    ]
    items = await adapter._input_for_responses(messages)
    assert items == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "describe"},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64,aGk=",
                    "detail": "high",
                },
            ],
        }
    ]


@pytest.mark.asyncio
async def test_multimodal_tool_output_is_preserved():
    adapter = ResponsesModelAdapter(RESPONSES_SPEC)
    items = await adapter._input_for_responses(
        [
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "text", "text": "screenshot"},
                    {
                        "type": "image",
                        "media_type": "image/png",
                        "artifact_ref": None,
                        "url": "data:image/png;base64,aGk=",
                        "detail": "low",
                    },
                ],
            }
        ]
    )
    assert items[0]["output"] == [
        {"type": "input_text", "text": "screenshot"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,aGk=",
            "detail": "low",
        },
    ]


@pytest.mark.asyncio
async def test_unmaterialized_artifact_ref_is_rejected_at_the_boundary():
    adapter = ResponsesModelAdapter(RESPONSES_SPEC)
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "media_type": "image/jpeg",
                    "artifact_ref": ArtifactRef(
                        artifact_id="art_1", content_type="image/jpeg"
                    ),
                    "url": None,
                }
            ],
        }
    ]
    with pytest.raises(RuntimeError):
        await adapter._input_for_responses(messages)


def test_chat_tools_flatten_to_responses_tool_declarations():
    tools: list[dict[str, object]] = [
        {
            "type": "function",
            "function": {
                "name": "current_time",
                "description": "Get UTC time",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert ResponsesModelAdapter._tools_for_responses(tools) == [
        {
            "type": "function",
            "name": "current_time",
            "description": "Get UTC time",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


class _StubResponsesClient:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: object):
        self.calls.append(kwargs)
        return self._response


@pytest.mark.asyncio
async def test_generate_parses_output_items_and_maps_finish_reason():
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(type="reasoning", summary=[]),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text='{"answer": "ok"}')],
            ),
            SimpleNamespace(
                type="function_call",
                call_id="call_9",
                name="locate_object",
                arguments='{"target": "bottle"}',
            ),
        ],
        usage=SimpleNamespace(input_tokens=31, output_tokens=7),
    )
    adapter = ResponsesModelAdapter(RESPONSES_SPEC)
    stub = _StubResponsesClient(response)
    adapter._client = stub

    result = await adapter.generate(
        ModelRequest(
            messages_ref=ArtifactRef(
                artifact_id="art_msgs", content_type="application/json"
            ),
            context_pack_ref=ArtifactRef(
                artifact_id="art_pack", content_type="application/json"
            ),
            system_prompt="be brief",
            messages=[{"role": "user", "content": "go"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "locate_object",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            max_output_tokens=256,
        ),
        ctx=_context(),
    )

    assert stub.calls[0]["model"] == "glm-5.3-flash"
    assert stub.calls[0]["instructions"] == "be brief"
    assert stub.calls[0]["store"] is False
    assert stub.calls[0]["input"] == [{"role": "user", "content": "go"}]
    assert stub.calls[0]["tools"] == [
        {
            "type": "function",
            "name": "locate_object",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    assert result.content == '{"answer": "ok"}'
    assert [call.tool_name for call in result.tool_calls] == ["locate_object"]
    assert result.tool_calls[0].raw_arguments == '{"target": "bottle"}'
    assert result.tool_calls[0].tool_call_id == "call_9"
    assert result.finish_reason == "tool_calls"
    assert result.usage.input_tokens == 31
    assert result.usage.output_tokens == 7


@pytest.mark.asyncio
async def test_generate_without_tool_calls_stops():
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="plain reply")],
            )
        ],
        usage=SimpleNamespace(input_tokens=5, output_tokens=2),
    )
    adapter = ResponsesModelAdapter(RESPONSES_SPEC)
    adapter._client = _StubResponsesClient(response)

    result = await adapter.generate(
        ModelRequest(
            messages_ref=ArtifactRef(
                artifact_id="art_msgs", content_type="application/json"
            ),
            context_pack_ref=ArtifactRef(
                artifact_id="art_pack", content_type="application/json"
            ),
            messages=[{"role": "user", "content": "hi"}],
            max_output_tokens=64,
        ),
        ctx=_context(),
    )
    assert result.content == "plain reply"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"


def test_agent_manager_dispatches_live_adapter_by_declared_protocol():
    manager = AgentManager("live")
    responses_adapter = manager._live_adapter_for(RESPONSES_SPEC)
    chat_adapter = manager._live_adapter_for(
        RESPONSES_SPEC.model_copy(
            update={"protocol": "openai_chat"}
        )
    )
    assert isinstance(responses_adapter, ResponsesModelAdapter)
    assert isinstance(chat_adapter, OpenAICompatibleModelAdapter)
    assert isinstance(create_model_adapter(RESPONSES_SPEC), ResponsesModelAdapter)


@pytest.mark.asyncio
async def test_incomplete_and_failed_statuses_are_not_collapsed_to_length():
    request = ModelRequest(
        messages_ref=ArtifactRef(
            artifact_id="art_msgs", content_type="application/json"
        ),
        context_pack_ref=ArtifactRef(
            artifact_id="art_pack", content_type="application/json"
        ),
        messages=[{"role": "user", "content": "hi"}],
        max_output_tokens=64,
    )
    incomplete = ResponsesModelAdapter(RESPONSES_SPEC)
    incomplete._client = _StubResponsesClient(
        SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="content_filter"),
            error=None,
            output=[],
            usage=None,
        )
    )
    result = await incomplete.generate(request, _context())
    assert result.finish_reason == "content_filter"

    failed = ResponsesModelAdapter(RESPONSES_SPEC)
    failed._client = _StubResponsesClient(
        SimpleNamespace(
            status="failed",
            error=SimpleNamespace(code="provider_failed"),
            output=[],
            usage=None,
        )
    )
    with pytest.raises(RuntimeError, match="provider_failed"):
        await failed.generate(request, _context())
