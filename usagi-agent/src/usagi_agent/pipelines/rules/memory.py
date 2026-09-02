"""Reusable recall rules supplied by the framework, not business scenarios."""
from __future__ import annotations

from typing import Literal

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_text, put_bytes
from usagi_agent.pipelines.rules.recall import RecallAdapterConfig
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.rules.stage import RecallRuleInput, RecallRuleOutput
from usagi_agent.types.context import RecallQuery


class LongTermMemoryRecallRule(RecallAdapterConfig):
    """One reusable recall-source rule; MemoryManager owns the actual algorithm."""

    type: Literal[StageType.RECALL] = StageType.RECALL
    name: str = "long_term_memory"

    async def recall(
        self, input: RecallRuleInput, runtime, context: RunContext
    ) -> RecallRuleOutput:
        text = await get_text(
            runtime.persistence.artifact_manager, input.normalized_input_ref
        )
        result = await runtime.memory_manager.recall(
            RecallQuery(query_id=f"memory:{context.run_id}", text=text),
            context.to_tool_context(),
        )
        ref = await put_bytes(
            runtime.persistence.artifact_manager,
            result.model_dump_json().encode(),
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"recall:memory:{context.run_id}",
        )
        cache = dict(input.recall_cache)
        cache["long_term_memory"] = ref
        return RecallRuleOutput(recall_cache=cache, recall_bundle_ref=ref)
