"""Unified recall for distilled long-term memory and tool observations."""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from usagi_agent.memory.manager import DefaultMemoryManager
from usagi_agent.models import GLM_5_2_MODEL
from usagi_agent.pipelines import ScenarioPipelineInitializer
from usagi_agent.pipelines.config.stage_config import SCENARIO_CONFIGS
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.registry import BootstrapSettings
from usagi_agent.server import Server, ServiceRuntimeInitializer
from usagi_agent.tools import ToolAdapter, ToolSpec
from usagi_agent.types.context import LongTermMemoryCandidate, RecallQuery
from usagi_agent.types.content import TextContentPart
from usagi_agent.types.model import ModelRequest, ModelResponse, ModelToolCall, ModelUsage
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.run import RunOptions, RunStartRequest


class _Request(BaseModel):
    query: str


def _tool_context(principal: str = "recall-user") -> ToolContext:
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id="default",
            principal=PrincipalRef(
                principal_kind="user", principal_opaque_id=principal
            ),
            authorization_scope=("tool.execute",),
            control_kind="run",
            control_id="run_recall_test",
            fencing_token=1,
        )
    )


class _WeatherTool(ToolAdapter):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:  # noqa: A003 - ToolAdapter contract
        return ToolSpec(
            name="weather_lookup",
            description="Look up weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments, context):
        self.calls += 1
        return {
            "weather": "sunny",
            "city": str(arguments.get("city", "")),
            "marker": f"run-{self.calls}",
        }


class _ScriptedToolThenFinalModel:
    """Call sequence: tool call(s) -> final. Captures every request."""

    adapter_ref = "test.recall_scripted_model"

    def __init__(self, tool_call_count: int = 1) -> None:
        self._tool_calls_left = tool_call_count
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, ctx) -> ModelResponse:
        self.requests.append(request)
        if self._tool_calls_left > 0:
            self._tool_calls_left -= 1
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        tool_name="weather_lookup",
                        tool_call_id=f"call-{self._tool_calls_left}",
                        raw_arguments=json.dumps({"city": "paris"}),
                    )
                ],
                finish_reason="tool_use",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        return ModelResponse(
            content="weather noted",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    async def health(self):
        return "healthy"


class _CaptureThenFinalModel:
    adapter_ref = "test.recall_capture_model"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, ctx) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            content="done",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    async def health(self):
        return "healthy"


def _build_server(model, tmp_path) -> tuple[Server, ServerRuntimeInitializer]:
    runtime = ServiceRuntimeInitializer.init(
        BootstrapSettings(
            model_execution_mode="scripted",
            memory_path=str(tmp_path / "memory.json"),
        )
    )
    runtime.agent_manager.set_model_adapter(model)
    runtime.agent_manager.create_agent(
        id="research_writer",
        input_schema="usagi.agent_request@1.0.0",
        output_schema="usagi.final_output@1.0.0",
        model=GLM_5_2_MODEL.model_copy(
            update={"context_window": 8_000, "default_max_output_tokens": 256}
        ),
        allowed_tools=("weather_lookup",),
    )
    runtime.tool_manager.register(_WeatherTool())
    ScenarioPipelineInitializer.init(runtime, SCENARIO_CONFIGS)
    return Server(runtime), runtime


def _request(key: str, query: str) -> RunStartRequest:
    return RunStartRequest(
        scenario_key="example.research_writer",
        request_idempotency_key=key,
        input=_Request(query=query),
        options=RunOptions(),
    )


async def _run_context(runtime, handle) -> ToolContext:
    """Build the run's real ToolContext so memory namespaces match."""
    snapshot = await runtime.persistence.execution_context_store.get(handle.run_id)
    control = await runtime.persistence.run_control_store.get(handle.run_id)
    assert snapshot is not None and control is not None
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id=snapshot.tenant_id,
            principal=snapshot.original_principal,
            authorization_scope=snapshot.authorization_scope,
            control_kind="run",
            control_id=handle.run_id,
            fencing_token=control.fencing_token,
        )
    )


@pytest.mark.asyncio
async def test_tool_result_recalled_into_next_run_model_request(tmp_path):
    first_model = _ScriptedToolThenFinalModel(tool_call_count=1)
    server, _ = _build_server(first_model, tmp_path)
    first = await server.start_agent(_request("recall-first", "weather in paris"))
    assert (await server.get_run(first.run_id)).kind == "completed"

    # A brand-new run/thread must see the previous run's tool result.
    second_model = _CaptureThenFinalModel()
    server.runtime.agent_manager.set_model_adapter(second_model)
    second = await server.start_agent(_request("recall-second", "what is the weather"))
    assert (await server.get_run(second.run_id)).kind == "completed"

    assert len(second_model.requests) == 1
    system_messages = [
        str(message.get("content", ""))
        for message in second_model.requests[0].messages
        if message.get("role") == "system"
    ]
    recalled = [text for text in system_messages if text.startswith("Recalled context")]
    assert recalled, system_messages
    payload = json.loads(recalled[0].split("\n", 1)[1])
    assert any("run-1" in json.dumps(group) for group in payload.values())
    await server.shutdown()


@pytest.mark.asyncio
async def test_same_tool_and_args_supersedes_to_freshest_result(tmp_path):
    model = _ScriptedToolThenFinalModel(tool_call_count=2)
    server, runtime = _build_server(model, tmp_path)
    handle = await server.start_agent(_request("recall-supersede", "weather paris"))
    assert (await server.get_run(handle.run_id)).kind == "completed"

    hits = await runtime.memory_manager.get_tool_observations(
        RecallQuery(query_id="q", text="weather paris"),
        await _run_context(runtime, handle),
    )
    assert len(hits) == 1
    assert "run-2" in hits[0].content
    assert hits[0].tool_name == "weather_lookup"
    assert hits[0].arguments_digest
    await server.shutdown()


@pytest.mark.asyncio
async def test_final_does_not_sediment_raw_events(tmp_path):
    """Final must leave long-term storage untouched: no raw-event extraction."""
    model = _ScriptedToolThenFinalModel(tool_call_count=1)
    server, runtime = _build_server(model, tmp_path)
    handle = await server.start_agent(_request("recall-no-extract", "weather paris"))
    assert (await server.get_run(handle.run_id)).kind == "completed"

    ctx = await _run_context(runtime, handle)
    session = await runtime.memory_manager.get_session_context(handle.thread_id, ctx)
    assert session.extracted_until is None, "no compaction happened in this run"

    generic = await runtime.memory_manager.get_long_term_memories(
        RecallQuery(query_id="q", text="weather paris"), ctx
    )
    # Long-term memory stays empty: no raw events, and tool observations live
    # in their own namespace, retrievable via get_tool_observations.
    assert not generic
    observations = await runtime.memory_manager.get_tool_observations(
        RecallQuery(query_id="q", text="weather paris"), ctx
    )
    assert len(observations) == 1
    await server.shutdown()


@pytest.mark.asyncio
async def test_long_term_crosses_sessions_short_term_does_not(tmp_path):
    """Recall-scope contract: LTM is cross-session, STM is session-only."""
    manager = DefaultMemoryManager(path=tmp_path / "memory.json")
    ctx = _tool_context(principal="isolation-user")

    # Session A: raw events, then compaction distills a summary.
    for value in ("alpha " * 20, "beta " * 20, "gamma " * 20):
        await manager.append_event(
            session_id="session-a", role="user",
            content_parts=[TextContentPart(text=value)], ctx=ctx
        )
    session_a = await manager.get_session_context("session-a", ctx)
    older = session_a.recent_event_ids[:2]
    await manager.apply_compaction(
        "session-a", older, ctx,
        summary="Session A discussed alpha and beta strategies.",
        memory_candidates=[
            LongTermMemoryCandidate(
                content="The user uses alpha and beta planning strategies.",
                importance=0.8,
            )
        ],
    )

    # Session B: one unrelated event, same principal.
    await manager.append_event(
        session_id="session-b", role="user",
        content_parts=[TextContentPart(text="unrelated question")], ctx=ctx
    )

    # Long-term: the distilled summary crosses sessions.
    ltm = await manager.get_long_term_memories(RecallQuery(query_id="q", text="alpha"), ctx)
    assert ltm
    assert any("alpha and beta planning strategies" in hit.content for hit in ltm)
    assert all(hit.type == "semantic" for hit in ltm)
    assert not await manager.get_long_term_memories(
        RecallQuery(query_id="other", text="alpha"),
        _tool_context(principal="other-user"),
    )

    # Raw and working memory stay session-local and are not recall sources.
    assert [event.search_text for event in await manager.list_events("session-b", ctx)] == [
        "unrelated question"
    ]
    assert len(await manager.list_events("session-a", ctx)) == 3
    assert len((await manager.get_session_context("session-a", ctx)).recent_event_ids) == 1


@pytest.mark.asyncio
async def test_compacted_raw_events_remain_archived_but_leave_working_memory(tmp_path):
    manager = DefaultMemoryManager(path=str(tmp_path / "memory.json"))
    ctx = _tool_context(principal="history-user")
    session = "history-thread"

    old = await manager.append_event(
        session_id=session, role="user",
        content_parts=[TextContentPart(
            text="the alpha weather station reported rain"
        )], ctx=ctx,
    )
    await manager.append_event(
        session_id=session, role="assistant",
        content_parts=[TextContentPart(text="noted the report")], ctx=ctx
    )
    current = await manager.append_event(
        session_id=session, role="user",
        content_parts=[TextContentPart(text="current weather question")], ctx=ctx
    )
    # Compact the two older events out of the recent window.
    session_ctx = await manager.get_session_context(session, ctx)
    older_ids = [
        event_id
        for event_id in session_ctx.recent_event_ids
        if event_id != current.event_id
    ]
    await manager.apply_compaction(session, older_ids, ctx)

    # The full transcript remains available for audit, but only the current event
    # remains in the model-visible short-term working set.
    assert old.event_id in {
        event.event_id for event in await manager.list_events(session, ctx)
    }
    assert (await manager.get_session_context(session, ctx)).recent_event_ids == [
        current.event_id
    ]
