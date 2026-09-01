"""Execute all configured Rules for each fixed business pipeline stage."""
from __future__ import annotations

from typing import TYPE_CHECKING

from usagi_agent.api.errors import SafeError
from usagi_agent.kernel.context import RunContext
from usagi_agent.pipelines.config import AgentPipelineConfig
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.stage import (
    ContextBuildRuleInput,
    ContextBuildRuleOutput,
    EndRuleInput,
    EndRuleOutput,
    ModelRuleInput,
    ModelRuleOutput,
    PreRecallRuleInput,
    PreRecallRuleOutput,
    RecallRuleInput,
    RecallRuleOutput,
    ResultProcessRuleInput,
    ResultProcessRuleOutput,
    RuleExecutionError,
    StatePatch,
)

if TYPE_CHECKING:
    from usagi_agent.server.runtime import ServerRuntime


class PipelineProcessor:
    """The six LangGraph-facing stage entry points for an Agent pipeline."""

    def __init__(self, pipeline: AgentPipelineConfig, runtime: "ServerRuntime") -> None:
        self.pipeline = pipeline
        self.runtime = runtime

    @staticmethod
    def _raise_on_error(error: RuleExecutionError, rule_name: str) -> None:
        message = f"rule {rule_name!r} failed"
        if error.message:
            message = f"{message}: {error.message}"
        raise SafeError(message, reason_code=error.reason_code)

    async def process_pre_recall(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        rule_input = PreRecallRuleInput(
            request_ref=state.get("request_ref", ""),
            normalized_input_ref=state.get("normalized_input_ref", ""),
            recall_plan_ref=state.get("recall_plan_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.pipeline.pre_recall:
            result = await config.pre_recall(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self._raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, PreRecallRuleOutput):
                raise TypeError("pre_recall rule returned an invalid output")
            changes = result.model_dump(exclude_none=True)
            stage_patch.update(changes)
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch

    async def process_recall(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        rule_input = RecallRuleInput(
            normalized_input_ref=state.get("normalized_input_ref", ""),
            recall_plan_ref=state.get("recall_plan_ref", ""),
            recall_cache=state.get("recall_cache", {}),
            recall_bundle_ref=state.get("recall_bundle_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.pipeline.recall:
            result = await config.recall(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self._raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, RecallRuleOutput):
                raise TypeError("recall rule returned an invalid output")
            changes = result.model_dump(exclude_none=True)
            stage_patch.update(changes)
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch

    async def process_context_build(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        rule_input = ContextBuildRuleInput(
            normalized_input_ref=state.get("normalized_input_ref", ""),
            recall_cache=state.get("recall_cache", {}),
            recall_bundle_ref=state.get("recall_bundle_ref", ""),
            tool_observation_refs=tuple(state.get("tool_observation_refs", [])),
            context_pack_ref=state.get("context_pack_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.pipeline.context_build:
            result = await config.build_context(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self._raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, ContextBuildRuleOutput):
                raise TypeError("context_build rule returned an invalid output")
            stage_patch["context_pack_ref"] = result.context_pack_ref
            rule_input = rule_input.model_copy(
                update={"context_pack_ref": result.context_pack_ref}
            )
        return stage_patch

    async def process_model(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        rule_input = ModelRuleInput(
            context_pack_ref=state.get("context_pack_ref", ""),
            iteration=state.get("iteration", 0),
            model_response_ref=state.get("model_response_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.pipeline.model:
            result = await config.model(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self._raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, ModelRuleOutput):
                raise TypeError("model rule returned an invalid output")
            stage_patch["model_response_ref"] = result.model_response_ref
            rule_input = rule_input.model_copy(
                update={"model_response_ref": result.model_response_ref}
            )
        return stage_patch

    async def process_result(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        rule_input = ResultProcessRuleInput(
            model_response_ref=state.get("model_response_ref", ""),
            iteration=state.get("iteration", 0),
            action_type=state.get("action_type", ""),
            action_hash=state.get("action_hash", ""),
            agent_action_ref=state.get("agent_action_ref", ""),
        )
        stage_patch: StatePatch = {}
        for config in self.pipeline.result_process:
            result = await config.process_result(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self._raise_on_error(result, config.name)
            if result is None:
                continue
            if not isinstance(result, ResultProcessRuleOutput):
                raise TypeError("result_process rule returned an invalid output")
            changes = result.model_dump()
            stage_patch.update(changes)
            rule_input = rule_input.model_copy(update=changes)
        return stage_patch

    async def process_end(
        self, state: AgentRunState, context: RunContext
    ) -> StatePatch:
        rule_input = EndRuleInput(
            action_type=state.get("action_type", ""),
            action_hash=state.get("action_hash", ""),
            agent_action_ref=state.get("agent_action_ref", ""),
            iteration=state.get("iteration", 0),
            pass_disposition=state.get("pass_disposition", ""),
            tool_observation_refs=tuple(state.get("tool_observation_refs", [])),
            final_output_ref=state.get("final_output_ref", ""),
        )
        stage_patch: StatePatch = {}
        new_observation_refs: list[str] = []
        for config in self.pipeline.end:
            result = await config.end(rule_input, self.runtime, context)
            if isinstance(result, RuleExecutionError):
                self._raise_on_error(result, config.name)
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
        return stage_patch
