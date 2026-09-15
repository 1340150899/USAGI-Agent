"""End stage: route the finished pass.

Pure disposition router. Action execution already happened in the
ResultProcess stage; this stage consumes the formed action_type plus any
observation/reason state and decides next_pass / run_completed / run_failed.
Custom end rules still run first and may override the disposition.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.artifacts import get_model
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules import EndAdapterConfig
from usagi_agent.pipelines.rules.stage import (
    EndRuleInput,
    EndRuleOutput,
    EndStagePatch,
    RuleExecutionError,
)
from usagi_agent.types.action import FinalAction, ToolObservation
from usagi_agent.prompts import STRUCTURED_OUTPUT_RETRY_PROMPT
from usagi_agent.types.content import merge_content_parts

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class EndProcessor(StageProcessor):
    def __init__(
        self,
        rules: tuple[EndAdapterConfig, ...],
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.rules = rules

    async def process(self, state: AgentRunState, context: RunContext) -> EndStagePatch:
        rule_input = EndRuleInput(
            action_type=state.get("action_type", ""),
            action_hash=state.get("action_hash", ""),
            agent_action_ref=state.get("agent_action_ref", ""),
            iteration=state.get("iteration", 0),
            pass_disposition=state.get("pass_disposition", ""),
            tool_observation_refs=tuple(state.get("tool_observation_refs", [])),
            final_output_ref=state.get("final_output_ref", ""),
            structured_output_attempts=state.get("structured_output_attempts", 0),
        )
        stage_patch: EndStagePatch = {}
        new_observation_refs: list[str] = []
        for config in self.rules:
            result = await self.run_rule(
                "end", config.name,
                config.end(rule_input, self.runtime, context),
            )
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, EndRuleOutput):
                raise TypeError("end rule returned an invalid output")
            stage_patch["pass_disposition"] = result.pass_disposition
            stage_patch["iteration"] = result.iteration
            if result.final_output_ref is not None:
                stage_patch["final_output_ref"] = result.final_output_ref
            for ref in result.tool_observation_refs:
                if ref not in new_observation_refs:
                    new_observation_refs.append(ref)
            if new_observation_refs:
                stage_patch["tool_observation_refs"] = new_observation_refs
            rule_input = rule_input.model_copy(
                update={
                    "pass_disposition": result.pass_disposition,
                    "iteration": result.iteration,
                    "tool_observation_refs": tuple(
                        dict.fromkeys(
                            (*rule_input.tool_observation_refs, *result.tool_observation_refs)
                        )
                    ),
                    "final_output_ref": (
                        result.final_output_ref
                        if result.final_output_ref is not None
                        else rule_input.final_output_ref
                    ),
                }
            )
        if "pass_disposition" not in stage_patch:
            result = await self._default_end(rule_input, context)
            stage_patch["pass_disposition"] = result.pass_disposition
            stage_patch["iteration"] = result.iteration
            if result.tool_observation_refs:
                stage_patch["tool_observation_refs"] = list(
                    result.tool_observation_refs
                )
            if result.final_output_ref is not None:
                stage_patch["final_output_ref"] = result.final_output_ref
        if context.output_contract is not None and stage_patch.get("pass_disposition") != "run_failed":
            structured = await self._structured_end(rule_input, context)
            stage_patch["pass_disposition"] = structured.pass_disposition
            stage_patch["iteration"] = structured.iteration
            if structured.final_output_ref is not None:
                stage_patch["final_output_ref"] = structured.final_output_ref
            if structured.structured_output_attempts is not None:
                stage_patch["structured_output_attempts"] = structured.structured_output_attempts
            if structured.structured_output_observation_ref is not None:
                stage_patch["structured_output_observation_ref"] = structured.structured_output_observation_ref
            if structured.reason_codes:
                stage_patch["reason_codes"] = list(structured.reason_codes)
        return stage_patch

    async def _structured_end(self, input: EndRuleInput, context: RunContext) -> EndRuleOutput:
        """Require a successful terminal-tool observation before completion."""
        contract = context.output_contract
        assert contract is not None
        failed = 0
        for ref in reversed(input.tool_observation_refs):
            observation = await get_model(
                self.runtime.persistence.artifact_manager, ref, ToolObservation
            )
            if observation is None or observation.tool_name != contract.terminal_tool_name:
                continue
            if observation.status == "success" and observation.output:
                output = observation.output
                if output.get("accepted") is True and output.get("schema_checksum") == contract.schema_checksum:
                    return EndRuleOutput(
                        pass_disposition="run_completed",
                        iteration=input.iteration + 1,
                        final_output_ref=ref,
                        structured_output_attempts=max(input.structured_output_attempts, failed),
                        structured_output_observation_ref=ref,
                    )
            failed += 1
        attempts = max(input.structured_output_attempts, failed)
        if input.action_type in {"final", "failure"}:
            attempts += 1
            await self.runtime.memory_manager.append_event(
                session_id=context.memory_session_id,
                role="system",
                content_parts=merge_content_parts(
                    text=STRUCTURED_OUTPUT_RETRY_PROMPT.render(
                        {"terminal_tool_name": contract.terminal_tool_name}
                    )
                ),
                ctx=context.to_tool_context(),
                metadata={"run_id": context.run_id, "reason_code": "structured_output.missing"},
                operation_id=f"structured-output-feedback:{context.run_id}:{attempts}",
            )
        if attempts > contract.max_retries:
            return EndRuleOutput(
                pass_disposition="run_failed", iteration=input.iteration + 1,
                structured_output_attempts=attempts,
                reason_codes=("structured_output.retry_exhausted",),
            )
        return EndRuleOutput(
            pass_disposition="next_pass", iteration=input.iteration + 1,
            structured_output_attempts=attempts,
            reason_codes=("structured_output.missing",) if input.action_type in {"final", "failure"} else (),
        )

    async def _default_end(self, input: EndRuleInput, context: RunContext) -> EndRuleOutput:
        iteration = input.iteration + 1
        # A rule or an earlier stage (for example, a budget gate) already
        # decided this pass failed; the router preserves that decision.
        if input.pass_disposition == "run_failed":
            return EndRuleOutput(pass_disposition="run_failed", iteration=iteration)
        if input.action_type in ("tool", "compaction"):
            # Observations (success or reason-carrying failure) already fed
            # back through the ResultProcess stage; the model adapts next pass.
            return EndRuleOutput(pass_disposition="next_pass", iteration=iteration)
        if input.action_type == "final":
            action = await get_model(
                self.runtime.persistence.artifact_manager,
                input.agent_action_ref,
                FinalAction,
            )
            return EndRuleOutput(
                pass_disposition="run_completed",
                iteration=iteration,
                final_output_ref=action.output_ref.artifact_id if action else "",
            )
        return EndRuleOutput(pass_disposition="run_failed", iteration=iteration)
