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
    RuleExecutionError,
    EndStagePatch,
)
from usagi_agent.types.action import FinalAction

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
        return stage_patch

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
