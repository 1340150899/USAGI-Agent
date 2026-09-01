from __future__ import annotations

from typing import cast

import pytest

from usagi_agent.api.errors import SafeError
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.config import AgentPipelineConfig
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processor import PipelineProcessor
from usagi_agent.pipelines.rules import (
    PreRecallAdapterConfig,
    PreRecallRuleInput,
    PreRecallRuleOutput,
    RuleExecutionError,
    StageType,
)
from usagi_agent.server.runtime import ServerRuntime
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
    processor = PipelineProcessor(pipeline, cast(ServerRuntime, object()))

    patch = await processor.process_pre_recall(
        AgentRunState(),
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
    processor = PipelineProcessor(
        AgentPipelineConfig(),
        cast(ServerRuntime, object()),
    )

    assert await processor.process_recall(AgentRunState(), _context()) == {}


@pytest.mark.asyncio
async def test_rule_returns_only_error_information_and_stops_the_stage():
    pipeline = AgentPipelineConfig(
        pre_recall=(
            _FailingPreRecallRule(name="failing", type=StageType.PRE_RECALL),
        )
    )
    processor = PipelineProcessor(pipeline, cast(ServerRuntime, object()))

    with pytest.raises(SafeError) as captured:
        await processor.process_pre_recall(AgentRunState(), _context())

    assert captured.value.reason_code == "rule.failed"
