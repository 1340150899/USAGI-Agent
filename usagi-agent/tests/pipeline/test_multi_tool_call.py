"""Multi-tool-call passes and parse-error feedback through the full pipeline."""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from usagi_agent.models import GLM_5_2_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.tools import ToolAdapter, ToolSpec
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelToolCall, ModelUsage
from usagi_agent.types.run import RunOptions, RunStartRequest


class _Request(BaseModel):
    query: str


class _CountingTool(ToolAdapter):
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:  # noqa: A003 - ToolAdapter contract
        return ToolSpec(requires_approval=False,
            name=self._name,
            description=f"echo tool {self._name}",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments, context):
        self.calls += 1
        return {"tool": self._name, "value": str(arguments.get("value", ""))}


class _MultiCallModel:
    """Call 1: two tool calls at once. Call 2: final answer."""

    adapter_ref = "test.multi_call_model"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, ctx) -> ModelResponse:
        self.requests.append(request)
        call_number = len(self.requests)
        if call_number == 1:
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        tool_name="alpha",
                        tool_call_id="multi-1",
                        raw_arguments=json.dumps({"value": "one"}),
                    ),
                    ModelToolCall(
                        tool_name="beta",
                        tool_call_id="multi-2",
                        raw_arguments=json.dumps({"value": "two"}),
                    ),
                ],
                finish_reason="tool_use",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        if call_number == 2:
            tool_messages = [
                message
                for message in request.messages
                if message.get("role") == "tool"
            ]
            assert len(tool_messages) == 2, tool_messages
            ids = {message.get("tool_call_id") for message in tool_messages}
            assert ids == {"multi-1", "multi-2"}
            payloads = [json.loads(str(message.get("content", "{}"))) for message in tool_messages]
            assert all(item.get("status") == "success" for item in payloads)
            return ModelResponse(
                content="both tools answered",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        raise AssertionError("pipeline made an unexpected extra model call")

    async def health(self):
        return "healthy"


class _ParseErrorThenFixModel:
    """Call 1: unparseable JSON arguments. Call 2: corrected call. Call 3: final."""

    adapter_ref = "test.parse_error_model"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, ctx) -> ModelResponse:
        self.requests.append(request)
        call_number = len(self.requests)
        if call_number == 1:
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        tool_name="alpha",
                        tool_call_id="bad-1",
                        raw_arguments="{value: broken json",
                    )
                ],
                finish_reason="tool_use",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        if call_number == 2:
            tool_messages = [
                message
                for message in request.messages
                if message.get("role") == "tool"
            ]
            assert len(tool_messages) == 1
            feedback = json.loads(str(tool_messages[0].get("content", "{}")))
            assert feedback.get("status") == "failed"
            assert feedback.get("error_code") == "tool.parse_error"
            assert "broken json" in str(feedback.get("reason", ""))
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        tool_name="alpha",
                        tool_call_id="good-1",
                        raw_arguments=json.dumps({"value": "fixed"}),
                    )
                ],
                finish_reason="tool_use",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        if call_number == 3:
            return ModelResponse(
                content="recovered from parse error",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        raise AssertionError("pipeline made an unexpected extra model call")

    async def health(self):
        return "healthy"


def _build_server(model, tools, tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            model_execution_mode="scripted",
            sqlite_path=str(tmp_path / "runtime.db"),
        )
    )
    server = Server(runtime)
    runtime.agent_manager.set_model_adapter(model)
    server.create_agent(
        id="research_writer",
        input_schema="usagi.agent_request@1.0.0",
        model=GLM_5_2_MODEL.model_copy(
            update={"context_window": 8_000, "default_max_output_tokens": 256}
        ),
        allowed_tools=tuple(tool.spec.name for tool in tools),
    )
    for tool in tools:
        runtime.tool_manager.register(tool)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    return server, runtime


@pytest.mark.asyncio
async def test_parallel_tool_calls_execute_serially_and_all_feed_back(tmp_path):
    alpha, beta = _CountingTool("alpha"), _CountingTool("beta")
    model = _MultiCallModel()
    server, _runtime = _build_server(model, (alpha, beta), tmp_path)
    handle = await server.create_session(
        RunStartRequest(
            scenario_key="example.research_writer",
            request_idempotency_key="multi-tool-pass",
            input=_Request(query="call both tools"),
            options=RunOptions(),
        )
    )
    outcome = await server.get_run(handle.run_id)
    assert outcome.kind == "completed"
    assert alpha.calls == 1
    assert beta.calls == 1
    assert len(model.requests) == 2
    await server.shutdown()


@pytest.mark.asyncio
async def test_parse_error_is_fed_back_and_model_recovers(tmp_path):
    alpha = _CountingTool("alpha")
    model = _ParseErrorThenFixModel()
    server, _runtime = _build_server(model, (alpha,), tmp_path)
    handle = await server.create_session(
        RunStartRequest(
            scenario_key="example.research_writer",
            request_idempotency_key="parse-error-recovery",
            input=_Request(query="call the tool"),
            options=RunOptions(),
        )
    )
    outcome = await server.get_run(handle.run_id)
    assert outcome.kind == "completed"
    # The malformed call never reached the adapter; the corrected one did.
    assert alpha.calls == 1
    assert len(model.requests) == 3
    await server.shutdown()
