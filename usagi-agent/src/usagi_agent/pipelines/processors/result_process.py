from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_model, put_model
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import ResultProcessAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
    RuleExecutionError,
    StatePatch,
)
from usagi_agent.types.action import FailureAction, FinalAction, SafeErrorRecord
from usagi_agent.types.model import ModelResponse

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class ResultProcessProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[ResultProcessAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules

    async def process(self, state: AgentRunState, context: RunContext) -> StatePatch:
        rule_input = ResultProcessRuleInput(
            model_response_ref=state.get("model_response_ref", ""),
            iteration=state.get("iteration", 0),
            action_type=state.get("action_type", ""),
            action_hash=state.get("action_hash", ""),
            agent_action_ref=state.get("agent_action_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.rules:
            result = await config.process_result(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, ResultProcessRuleOutput):
                raise TypeError("result_process rule returned an invalid output")
            changes = result.model_dump()
            stage_patch.update(changes)
            rule_input = rule_input.model_copy(update=changes)
        if "action_type" not in stage_patch:
            changes = await self._default_result(rule_input, context)
            stage_patch.update(changes.model_dump())
        return stage_patch

    async def _default_result(
        self, input: ResultProcessRuleInput, context: RunContext
    ) -> ResultProcessRuleOutput:
        response = await get_model(
            self.runtime.persistence.artifact_manager,
            input.model_response_ref,
            ModelResponse,
        )
        if response is None:
            action = FailureAction(
                error=SafeErrorRecord(
                    reason_code="model.no_response", message="missing"
                ),
                source_stage="result_process",
            )
            action_type = "failure"
        elif response.tool_calls:
            action, action_type = response.tool_calls[0], "tool"
        else:
            action = FinalAction(
                output_ref=response.content_ref,
                output_schema="usagi.final_output@1.0.0",
            )
            action_type = "final"
        if response is not None and response.content:
            await self.runtime.memory_manager.append_event(
                session_id=context.thread_id,
                role="assistant",
                content=response.content,
                ctx=context.to_tool_context(),
                metadata={"run_id": context.run_id},
            )
        if response is not None and response.context_update is not None:
            await self.runtime.memory_manager.apply_context_update(
                context.thread_id,
                context.to_tool_context(),
                **response.context_update.model_dump(
                    exclude_none=True, exclude={"compacted"}
                ),
            )
        # Compaction belongs exclusively to ContextBuildProcessor. In particular,
        # processing a model result must not perform a second compaction pass.
        ref = await put_model(
            self.runtime.persistence.artifact_manager,
            action,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"action:{context.run_id}:{input.iteration}",
        )
        return ResultProcessRuleOutput(
            action_type=action_type,
            action_hash=hashlib.sha256(action.model_dump_json().encode()).hexdigest(),
            agent_action_ref=ref,
        )
