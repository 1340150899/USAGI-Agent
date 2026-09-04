"""Framework rule: apply an accepted compaction result to thread memory.

Runs inside the ResultProcess stage after the stage's fixed action formation
has validated the compaction response and persisted the ContextUpdate. The
rule owns the memory mutation only — detection and prompt building stay in
ContextBuild; validation of an INVALID compaction belongs to formation,
which emits a FailureAction (graceful run_failed). This rule never
re-validates that, so there is exactly one error path per condition.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import ModelContextEnvelope, get_model
from usagi_agent.pipelines.rules.result_process import ResultProcessAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
)
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.types.context import CompactionResult

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class CompactionApplyRule(ResultProcessAdapterConfig):
    name: str = "compaction_apply"
    type: Literal[StageType.RESULT_PROCESS] = StageType.RESULT_PROCESS

    async def process_result(
        self,
        input: ResultProcessRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> ResultProcessRuleOutput | None:
        if input.context_operation != "compaction" or input.action_type != "compaction":
            return None
        artifact_manager = runtime.persistence.artifact_manager
        result = await get_model(
            artifact_manager, input.agent_action_ref, CompactionResult
        )
        envelope = await get_model(
            artifact_manager, input.context_pack_ref, ModelContextEnvelope
        )
        if result is None or envelope is None:
            # Unreachable in-contract: formation only emits action_type
            # "compaction" after validating and persisting both artifacts.
            # Validation of invalid compactions is formation's job.
            return None
        await runtime.memory_manager.apply_compaction(
            context.thread_id,
            envelope.compacted_event_ids,
            context.to_tool_context(),
            memory_candidates=result.long_term_memory_candidates,
            **result.context_update.model_dump(
                exclude_none=True, exclude={"compacted"}
            ),
        )
        return None
