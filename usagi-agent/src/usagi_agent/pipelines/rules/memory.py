"""Framework recall rules: each source parses and emits one RecallBundle."""
from __future__ import annotations

from typing import Literal

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_text, put_recall_bundle
from usagi_agent.pipelines.rules.recall import RecallAdapterConfig
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.pipelines.rules.stage import RecallRuleInput, RecallRuleOutput
from usagi_agent.types.context import RecallQuery


async def _query_text(input: RecallRuleInput, runtime) -> str:
    return await get_text(
        runtime.persistence.artifact_manager, input.normalized_input_ref
    )


async def _emit(
    *,
    source: str,
    hits: list[dict[str, object]],
    input: RecallRuleInput,
    runtime,
    context: RunContext,
) -> RecallRuleOutput:
    ref = await put_recall_bundle(
        runtime.persistence.artifact_manager,
        source=source,
        hits=hits,
        tenant_id=context.tenant_id,
        scope_id=context.run_id,
        run_id=context.run_id,
    )
    cache = dict(input.recall_cache)
    cache[source] = ref
    return RecallRuleOutput(recall_cache=cache, recall_bundle_ref=ref)


class LongTermMemoryRecallRule(RecallAdapterConfig):
    """Recall distilled long-term memories — cross-session by definition.

    Long-term memory is produced ONLY from durable candidates explicitly
    selected by the LLM during compaction. The short-term summary and raw
    conversation never enter this store, so a later session cannot recall
    a previous session's verbatim events or transient working summary.
    """

    type: Literal[StageType.RECALL] = StageType.RECALL
    name: str = "long_term_memory"

    async def recall(
        self, input: RecallRuleInput, runtime, context: RunContext
    ) -> RecallRuleOutput:
        memories = await runtime.memory_manager.get_long_term_memories(
            RecallQuery(query_id=f"memory:{context.run_id}", text=await _query_text(input, runtime)),
            context.to_tool_context(),
        )
        hits = [hit.model_dump(mode="json") for hit in memories]
        return await _emit(
            source="long_term_memory", hits=hits, input=input, runtime=runtime, context=context
        )


class ToolObservationRecallRule(RecallAdapterConfig):
    """Recall past successful tool results for reuse (cross-session per principal)."""

    type: Literal[StageType.RECALL] = StageType.RECALL
    name: str = "tool_observations"

    async def recall(
        self, input: RecallRuleInput, runtime, context: RunContext
    ) -> RecallRuleOutput:
        observations = await runtime.memory_manager.get_tool_observations(
            RecallQuery(query_id=f"tool-obs:{context.run_id}", text=await _query_text(input, runtime)),
            context.to_tool_context(),
        )
        hits = [item.model_dump(mode="json") for item in observations]
        return await _emit(
            source="tool_observations", hits=hits, input=input, runtime=runtime, context=context
        )
