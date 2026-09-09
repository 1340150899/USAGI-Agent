"""Framework PreRecall rule that classifies and normalizes run input."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_model, put_side_effect_receipt
from usagi_agent.pipelines.rules.pre_recall import PreRecallAdapterConfig
from usagi_agent.pipelines.rules.stage import PreRecallRuleInput, PreRecallRuleOutput
from usagi_agent.pipelines.rules.stage_type import StageType
from usagi_agent.types.content import merge_content_parts
from usagi_agent.types.run import RunInputEnvelope

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class RunInputNormalizationRule(PreRecallAdapterConfig):
    """Turn one opaque request Artifact into model/memory input artifacts."""

    name: str = "run_input_normalization"
    type: Literal[StageType.PRE_RECALL] = StageType.PRE_RECALL

    async def pre_recall(
        self,
        input: PreRecallRuleInput,
        runtime: "ServerRuntime",
        context: RunContext,
    ) -> PreRecallRuleOutput | None:
        envelope = await get_model(
            runtime.persistence.artifact_manager,
            input.request_ref,
            RunInputEnvelope,
        )
        if envelope is None:
            raise RuntimeError("missing run input envelope artifact")

        normalized = json.dumps(envelope.input, ensure_ascii=False).encode()
        operation_id = f"memory:user-event:{context.run_id}"
        event = await runtime.memory_manager.append_event(
            session_id=context.memory_session_id,
            role="user",
            content_parts=merge_content_parts(
                text=normalized.decode(), parts=envelope.content_parts
            ),
            ctx=context.to_tool_context(),
            metadata={"run_id": context.run_id},
            operation_id=operation_id,
        )
        receipt_ref = await put_side_effect_receipt(
            runtime.persistence.artifact_manager,
            operation_id=operation_id,
            effect_type="memory.append_event",
            result_ref=event.event_id,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
        )
        return PreRecallRuleOutput(
            context_compaction_mode=(
                envelope.options.context_compaction
                if input.iteration == 0
                else None
            ),
            side_effect_receipt_refs=(receipt_ref,),
        )
