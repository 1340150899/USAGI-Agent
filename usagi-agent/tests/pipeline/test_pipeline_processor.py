from __future__ import annotations

from typing import cast

import pytest

from examples.structured_agent.run import build_server
from usagi_agent.api.errors import SafeError
from usagi_agent.kernel.context import RunContext
from usagi_agent.memory.manager import DefaultMemoryManager
from usagi_agent.memory.types import PreparedContext, RawEvent, SessionContext
from usagi_agent.pipelines.artifacts import get_model
from usagi_agent.pipelines.config import AgentPipelineConfig
from usagi_agent.pipelines.config import ContextBuildPipelineConfig
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processor import PipelineProcessor
from usagi_agent.pipelines.processors.context_build import ContextBuildProcessor
from usagi_agent.pipelines.processors.model import ModelProcessor
from usagi_agent.pipelines.processors.pre_recall import PreRecallProcessor
from usagi_agent.pipelines.processors.recall import RecallProcessor
from usagi_agent.pipelines.rules import (
    ContextFilterAdapterConfig,
    ContextRankAdapterConfig,
    PreRecallAdapterConfig,
    PreRecallRuleInput,
    PreRecallRuleOutput,
    ModelRuleAdapterConfig,
    ModelRuleInput,
    ModelRuleOutput,
    RuleExecutionError,
    StageType,
)
from usagi_agent.server.runtime import ServerRuntime
from usagi_agent.agents.spec import AgentSpec
from usagi_agent.types.refs import PrincipalRef
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.content import TextContentPart
from usagi_agent.types.model import ModelRequest


class _FirstPreRecallRule(PreRecallAdapterConfig):
    calls: list[str]

    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context
    ) -> PreRecallRuleOutput:
        self.calls.append(self.name)
        return PreRecallRuleOutput(recall_plan_ref="first-plan")


class _SecondPreRecallRule(PreRecallAdapterConfig):
    calls: list[str]

    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context
    ) -> PreRecallRuleOutput:
        self.calls.append(self.name)
        assert input.recall_plan_ref == "first-plan"
        return PreRecallRuleOutput(recall_plan_ref="plan")


class _FailingPreRecallRule(PreRecallAdapterConfig):
    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context
    ) -> RuleExecutionError:
        return RuleExecutionError(reason_code="rule.failed", message="expected failure")


class _StaticModelRule(ModelRuleAdapterConfig):
    async def model(self, input: ModelRuleInput, runtime, context) -> ModelRuleOutput:
        return ModelRuleOutput(model_response_ref="response-from-rule")


_context_calls: list[str] = []


class _ContextFilter(ContextFilterAdapterConfig):
    async def filter_context(self, recalled_context, runtime, context):
        _context_calls.append(f"filter:{self.name}:{recalled_context}")
        discarded = "0" if self.name == "first" else "1"
        return recalled_context == discarded


class _ContextRanker(ContextRankAdapterConfig):
    async def rank_context(self, recalled_contexts, runtime, context):
        _context_calls.append(f"rank:{self.name}")
        recalled_contexts.reverse()


def _context() -> RunContext:
    return RunContext(
        run_id="run-1",
        thread_id="thread-1",
        tenant_id="tenant-1",
        principal=PrincipalRef(
            principal_kind="user",
            principal_opaque_id="user-1",
        ),
        authorization_scope=(),
        deadline=None,
        fencing_token=1,
    )


@pytest.mark.asyncio
async def test_stage_processor_runs_all_rules_in_order_with_accumulated_state():
    calls: list[str] = []
    pipeline = AgentPipelineConfig(
        pre_recall=(
            _FirstPreRecallRule(
                name="first",
                type=StageType.PRE_RECALL,
                calls=calls,
            ),
            _SecondPreRecallRule(
                name="second",
                type=StageType.PRE_RECALL,
                calls=calls,
            ),
        )
    )
    processor = PreRecallProcessor(
        pipeline.pre_recall,
        cast(ServerRuntime, object()),
        cast(AgentSpec, object()),
    )

    patch = await processor.process(
        AgentRunState(request_ref="request"),
        _context(),
    )

    assert cast(_FirstPreRecallRule, pipeline.pre_recall[0]).calls == ["first"]
    assert cast(_SecondPreRecallRule, pipeline.pre_recall[1]).calls == ["second"]
    assert patch.get("recall_plan_ref") == "plan"
    assert patch.get("recall_cache") == {}
    assert patch.get("model_response_ref") == ""
    assert patch.get("tool_action_refs") == []
    assert patch.get("pass_disposition") == ""


@pytest.mark.asyncio
async def test_stage_processor_allows_an_empty_stage():
    processor = RecallProcessor(
        AgentPipelineConfig().recall,
        cast(ServerRuntime, object()),
        cast(AgentSpec, object()),
    )

    assert await processor.process(AgentRunState(), _context()) == {}


@pytest.mark.asyncio
async def test_model_processor_uses_rule_output_without_implicit_model_call():
    processor = ModelProcessor(
        (
            _StaticModelRule(name="static", type=StageType.MODEL),
        ),
        cast(ServerRuntime, object()),
        cast(AgentSpec, type("Agent", (), {"id": "agent"})()),
    )

    patch = await processor.process(AgentRunState(), _context())

    assert patch == {"model_response_ref": "response-from-rule"}


@pytest.mark.asyncio
async def test_rule_returns_only_error_information_and_stops_the_stage():
    pipeline = AgentPipelineConfig(
        pre_recall=(
            _FailingPreRecallRule(name="failing", type=StageType.PRE_RECALL),
        )
    )
    processor = PreRecallProcessor(
        pipeline.pre_recall,
        cast(ServerRuntime, object()),
        cast(AgentSpec, object()),
    )

    with pytest.raises(SafeError) as captured:
        await processor.process(
            AgentRunState(request_ref="request"), _context()
        )

    assert captured.value.reason_code == "rule.failed"


@pytest.mark.asyncio
async def test_context_build_runs_filter_chain_before_rank_chain():
    _context_calls.clear()
    config = ContextBuildPipelineConfig(
        filters=(
            _ContextFilter(name="first"),
            _ContextFilter(name="second"),
        ),
        rankers=(_ContextRanker(name="rank"),),
    )
    processor = ContextBuildProcessor(
        config, cast(ServerRuntime, object()), cast(AgentSpec, object())
    )
    recalled_contexts: dict[str, list[object]] = {"long_term_memory": ["0", "1", "2", "3"]}

    recalled_contexts = await processor._run_filters(recalled_contexts, _context())
    await processor._run_rankers(recalled_contexts, _context())

    assert _context_calls == [
        "filter:first:0",
        "filter:first:1",
        "filter:first:2",
        "filter:first:3",
        "filter:second:1",
        "filter:second:2",
        "filter:second:3",
        "rank:rank",
    ]
    assert recalled_contexts == {"long_term_memory": ["3", "2"]}


def test_compaction_request_batches_an_oldest_prefix_within_model_window():
    server = build_server()
    base_agent = server.runtime.agent_manager.get("research_writer")
    agent = base_agent.model_copy(
        update={
            "model": base_agent.model.model_copy(
                update={"context_window": 800, "default_max_output_tokens": 100}
            )
        }
    )
    processor = ContextBuildProcessor(
        ContextBuildPipelineConfig(), server.runtime, agent
    )
    events = [
        RawEvent(
            event_id=f"event-{index}",
            session_id="thread",
            role="user",
            content_parts=[TextContentPart(text=str(index) * 300)],
        )
        for index in range(4)
    ]
    prepared = PreparedContext(
        session=SessionContext(), events_to_compact=events
    )

    batch = processor._fit_compaction_batch(prepared)

    assert 0 < len(batch) < len(events)
    assert [event.event_id for event in batch] == [
        event.event_id for event in events[: len(batch)]
    ]
    payload = processor._compaction_payload(prepared, batch)
    assert processor._compaction_input_tokens(payload) <= 700


def test_compaction_request_rejects_one_event_larger_than_model_window():
    server = build_server()
    base_agent = server.runtime.agent_manager.get("research_writer")
    agent = base_agent.model_copy(
        update={
            "model": base_agent.model.model_copy(
                update={"context_window": 500, "default_max_output_tokens": 100}
            )
        }
    )
    processor = ContextBuildProcessor(
        ContextBuildPipelineConfig(), server.runtime, agent
    )
    prepared = PreparedContext(
        session=SessionContext(),
        events_to_compact=[
            RawEvent(
                event_id="huge",
                session_id="thread",
                role="user",
                content_parts=[TextContentPart(text="x" * 3_000)],
            )
        ],
    )

    with pytest.raises(RuntimeError, match="first event"):
        processor._fit_compaction_batch(prepared)


@pytest.mark.asyncio
async def test_context_is_compressed_once_and_only_model_stage_invokes_model(monkeypatch):
    server = build_server()
    runtime = server.runtime
    scenario = runtime.scenario_registry.get("example.research_writer")
    processor = PipelineProcessor(scenario.config, runtime)
    prepare_calls = 0
    generate_calls = 0
    original_prepare = runtime.memory_manager.prepare_context
    original_generate = runtime.agent_manager.generate

    async def prepare_spy(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        return await original_prepare(*args, **kwargs)

    async def generate_spy(*args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return await original_generate(*args, **kwargs)

    monkeypatch.setattr(runtime.memory_manager, "prepare_context", prepare_spy)
    monkeypatch.setattr(runtime.agent_manager, "generate", generate_spy)

    state: AgentRunState = {}
    state.update(
        cast(AgentRunState, await processor.process_context_build(state, _context()))
    )
    assert prepare_calls == 1
    assert generate_calls == 0

    state.update(cast(AgentRunState, await processor.process_model(state, _context())))
    assert prepare_calls == 1
    assert generate_calls == 1

    state.update(cast(AgentRunState, await processor.process_result(state, _context())))
    assert prepare_calls == 1
    assert generate_calls == 1
    await server.shutdown()


@pytest.mark.asyncio
async def test_llm_compaction_is_applied_then_restarts_the_pipeline(
    tmp_path,
):
    server = build_server()
    runtime = server.runtime
    runtime.memory_manager = DefaultMemoryManager(path=tmp_path / "memory.json")
    scenario = runtime.scenario_registry.get("example.research_writer")
    processor = PipelineProcessor(scenario.config, runtime)
    context = _context()
    tool_context = context.to_tool_context()
    for value in ("alpha " * 20, "beta " * 20, "gamma " * 20):
        await runtime.memory_manager.append_event(
            session_id=context.thread_id,
            role="user",
            content_parts=[TextContentPart(text=value)],
            ctx=tool_context,
        )

    state: AgentRunState = {"context_compaction_mode": "force"}
    state.update(
        cast(AgentRunState, await processor.process_context_build(state, context))
    )
    assert state.get("context_operation") == "compaction"
    assert state.get("context_compaction_mode") == "auto"

    state.update(cast(AgentRunState, await processor.process_model(state, context)))
    state.update(cast(AgentRunState, await processor.process_result(state, context)))
    assert state.get("action_type") == "compaction"
    assert state.get("side_effect_receipt_refs")

    session = await runtime.memory_manager.get_session_context(
        context.thread_id, tool_context
    )
    assert session.compacted_until == session.extracted_until
    assert session.summary == "Compacted 2 earlier events."
    # Long-term memory holds the distilled summary (semantic), never the
    # verbatim "alpha" event. Raw events remain archived but are not recalled.
    long_term = await runtime.memory_manager.get_long_term_memories(
        RecallQuery(query_id="q", text="durable information"), tool_context
    )
    assert long_term
    assert all(
        hit.type == "semantic" and hit.key.startswith("compaction-extract:")
        for hit in long_term
    )
    assert any(
        "alpha" in event.search_text
        for event in await runtime.memory_manager.list_events(
            context.thread_id, tool_context
        )
    )

    state.update(cast(AgentRunState, await processor.process_end(state, context)))
    assert state.get("pass_disposition") == "next_pass"
    state.update(
        cast(AgentRunState, await processor.process_context_build(state, context))
    )
    assert state.get("context_operation") == "normal"
    request = await get_model(
        runtime.persistence.artifact_manager,
        state.get("model_request_ref", ""),
        ModelRequest,
    )
    assert request is not None
    serialized_messages = str(request.messages)
    assert "Compacted 2 earlier events." in serialized_messages
    assert "alpha alpha" not in serialized_messages
    assert "beta beta" not in serialized_messages
    await server.shutdown()
