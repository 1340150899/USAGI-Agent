from __future__ import annotations

from typing import cast

import pytest

from examples.structured_agent.run import build_server
from usagi_agent.api.errors import SafeError
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.config import AgentPipelineConfig
from usagi_agent.pipelines.config import ContextBuildPipelineConfig
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processor import PipelineProcessor
from usagi_agent.pipelines.processors.context_build import ContextBuildProcessor
from usagi_agent.pipelines.processors.pre_recall import PreRecallProcessor
from usagi_agent.pipelines.processors.recall import RecallProcessor
from usagi_agent.pipelines.rules import (
    ContextFilterAdapterConfig,
    ContextRankAdapterConfig,
    PreRecallAdapterConfig,
    PreRecallRuleInput,
    PreRecallRuleOutput,
    RuleExecutionError,
    StageType,
)
from usagi_agent.server.runtime import ServerRuntime
from usagi_agent.agents.spec import AgentSpec
from usagi_agent.types.refs import PrincipalRef


class _FirstPreRecallRule(PreRecallAdapterConfig):
    calls: list[str]

    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context
    ) -> PreRecallRuleOutput:
        self.calls.append(self.name)
        return PreRecallRuleOutput(normalized_input_ref="normalized")


class _SecondPreRecallRule(PreRecallAdapterConfig):
    calls: list[str]

    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context
    ) -> PreRecallRuleOutput:
        self.calls.append(self.name)
        assert input.normalized_input_ref == "normalized"
        return PreRecallRuleOutput(recall_plan_ref="plan")


class _FailingPreRecallRule(PreRecallAdapterConfig):
    async def pre_recall(
        self, input: PreRecallRuleInput, runtime, context
    ) -> RuleExecutionError:
        return RuleExecutionError(reason_code="rule.failed", message="expected failure")


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
        AgentRunState(normalized_input_ref="input"),
        _context(),
    )

    assert cast(_FirstPreRecallRule, pipeline.pre_recall[0]).calls == ["first"]
    assert cast(_SecondPreRecallRule, pipeline.pre_recall[1]).calls == ["second"]
    assert patch == {
        "normalized_input_ref": "normalized",
        "recall_plan_ref": "plan",
    }


@pytest.mark.asyncio
async def test_stage_processor_allows_an_empty_stage():
    processor = RecallProcessor(
        AgentPipelineConfig().recall,
        cast(ServerRuntime, object()),
        cast(AgentSpec, object()),
    )

    assert await processor.process(AgentRunState(), _context()) == {}


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
            AgentRunState(normalized_input_ref="input"), _context()
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
    recalled_contexts: list[object] = ["0", "1", "2", "3"]

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
    assert recalled_contexts == ["3", "2"]


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
