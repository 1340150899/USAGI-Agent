from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from usagi_agent.models import GLM_5_2_MODEL
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.artifacts import get_text
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.tools import ToolAdapter, ToolSpec
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelToolCall, ModelUsage
from usagi_agent.types.run import RunOptions, RunStartRequest


class _Request(BaseModel):
    query: str


class _LargeSearchTool(ToolAdapter):
    spec = ToolSpec(
        name="web_search",
        description="Return a mocked search result.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self, arguments: dict[str, object], context: ToolContext
    ) -> dict[str, object]:
        self.calls += 1
        return {
            "query": str(arguments["query"]),
            "summary": "source " * 70,
        }


class _ProtocolCheckingModel:
    adapter_ref = "test.protocol_checking_model"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(
        self, request: ModelRequest, ctx: ToolContext
    ) -> ModelResponse:
        self.requests.append(request)
        call_number = len(self.requests)
        if call_number == 1:
            assert request.prompt_ref != "usagi.context_compaction_prompt@1.1.0"
            assert request.tools
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        tool_name="web_search",
                        tool_call_id="mock-call-1",
                        raw_arguments=json.dumps({"query": "mock query"}),
                    )
                ],
                finish_reason="tool_use",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        if call_number == 2:
            assert request.prompt_ref != "usagi.context_compaction_prompt@1.1.0"
            assistant_calls = [
                message
                for message in request.messages
                if message.get("role") == "assistant"
                and message.get("tool_calls")
            ]
            tool_results = [
                message
                for message in request.messages
                if message.get("role") == "tool"
            ]
            assert len(assistant_calls) == 1
            assert len(tool_results) == 1
            calls = assistant_calls[0].get("tool_calls")
            assert isinstance(calls, list)
            first_call = calls[0]
            assert isinstance(first_call, dict)
            assert first_call["id"] == "mock-call-1"
            assert tool_results[0]["tool_call_id"] == "mock-call-1"
            return ModelResponse(
                content=json.dumps(
                    {
                        "answer": "mock final answer",
                        "context_update": {
                            "open_tasks": [],
                            "summary": "Mocked research completed.",
                            # Provider drift in optional state must not prevent
                            # ResultProcess from extracting a valid answer.
                            "facts": ["invalid shape"],
                        },
                    }
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        raise AssertionError("pipeline made an unexpected extra model call")

    async def health(self):
        return "healthy"


@pytest.mark.asyncio
async def test_mocked_model_runs_tool_protocol_and_final_answer(tmp_path):
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            model_execution_mode="scripted",
            memory_path=str(tmp_path / "memory.json"),
        )
    )
    model = _ProtocolCheckingModel()
    runtime.agent_manager.set_model_adapter(model)
    runtime.agent_manager.create_agent(
        id="research_writer",
        input_schema="usagi.agent_request@1.0.0",
        output_schema="usagi.final_output@1.0.0",
        model=GLM_5_2_MODEL.model_copy(
            update={"context_window": 600, "default_max_output_tokens": 64}
        ),
        allowed_tools=("web_search",),
    )
    search = _LargeSearchTool()
    runtime.tool_manager.register(search)
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    server = Server(runtime)

    handle = await server.start_agent(
        RunStartRequest(
            scenario_key="example.research_writer",
            request_idempotency_key="mock-full-chain",
            input=_Request(query="q" * 1_000),
            options=RunOptions(),
        )
    )
    outcome = await server.get_run(handle.run_id)

    assert outcome.kind == "completed"
    assert await get_text(
        runtime.persistence.artifact_manager, outcome.result_ref.artifact_id
    ) == "mock final answer"
    assert search.calls == 1
    assert len(model.requests) == 2
    assert [
        request.prompt_ref == "usagi.context_compaction_prompt@1.1.0"
        for request in model.requests
    ] == [False, False]

    snapshot = await runtime.persistence.execution_context_store.get(handle.run_id)
    control = await runtime.persistence.run_control_store.get(handle.run_id)
    assert snapshot is not None
    assert control is not None
    tool_context = ToolContext(
        execution=GovernedExecutionContext(
            tenant_id=snapshot.tenant_id,
            principal=snapshot.original_principal,
            authorization_scope=snapshot.authorization_scope,
            control_kind="run",
            control_id=handle.run_id,
            fencing_token=control.fencing_token,
        )
    )
    session = await runtime.memory_manager.get_session_context(
        handle.thread_id, tool_context
    )
    assert session.compacted_until is None
    assert session.open_tasks == []
    assert session.summary == ""
    await server.shutdown()
