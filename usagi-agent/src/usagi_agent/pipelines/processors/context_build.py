from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from usagi_agent.kernel.context import RunContext
from usagi_agent.memory.types import ContextPolicy, PreparedContext
from usagi_agent.pipelines.artifacts import get_text, put_model
from usagi_agent.pipelines.config.context_build import ContextBuildPipelineConfig
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules.stage import RuleExecutionError, StatePatch
from usagi_agent.ports import MemoryRecallResult
from usagi_agent.prompts import prompt_for_agent
from usagi_agent.tools import to_model_tool
from usagi_agent.types.model import ModelRequest
from usagi_agent.types.refs import ArtifactRef

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class ModelContextEnvelope(BaseModel):
    """Artifact passed from Context Build to Model."""

    messages: list[dict[str, object]] = Field(default_factory=list)
    recalled_memories: list[object] = Field(default_factory=list)
    estimated_tokens: int = 0
    compacted: bool = False


class ContextBuildProcessor(StageProcessor):
    def __init__(
        self,
        config: ContextBuildPipelineConfig,
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.config = config

    async def process(self, state: AgentRunState, context: RunContext) -> StatePatch:
        recalled_contexts = await self._load_recalled_contexts(state)
        recalled_contexts = await self._run_filters(recalled_contexts, context)
        await self._run_rankers(recalled_contexts, context)
        prepared = await self._compress_once(context)
        context_pack_ref, model_request_ref = await self._build_prompt(
            prepared,
            recalled_contexts,
            tuple(state.get("tool_observation_refs", [])),
            context,
        )
        return {
            "context_pack_ref": context_pack_ref,
            "model_request_ref": model_request_ref,
        }

    async def _load_recalled_contexts(self, state: AgentRunState) -> list[object]:
        memory_ref = state.get("recall_cache", {}).get("long_term_memory")
        if not memory_ref:
            return []
        payload = await get_text(self.runtime.persistence.artifact_manager, memory_ref)
        recalled = MemoryRecallResult.model_validate_json(payload)
        return list(recalled.hits)

    async def _run_filters(
        self, recalled_contexts: list[object], context: RunContext
    ) -> list[object]:
        remaining = recalled_contexts
        for rule in self.config.filters:
            filtered: list[object] = []
            for recalled_context in remaining:
                result = await rule.filter_context(
                    recalled_context, self.runtime, context
                )
                if isinstance(result, RuleExecutionError):
                    self.raise_on_error(result, rule.name)
                if not isinstance(result, bool):
                    raise TypeError("context filter must return bool")
                if not result:
                    filtered.append(recalled_context)
            remaining = filtered
        return remaining

    async def _run_rankers(
        self, recalled_contexts: list[object], context: RunContext
    ) -> None:
        for rule in self.config.rankers:
            result = await rule.rank_context(
                recalled_contexts, self.runtime, context
            )
            if isinstance(result, RuleExecutionError):
                self.raise_on_error(result, rule.name)
            if result is not None:
                raise TypeError("context ranker must sort in place and return None")

    async def _compress_once(self, context: RunContext) -> PreparedContext:
        model = self.agent.model
        return await self.runtime.memory_manager.prepare_context(
            context.thread_id,
            context.to_tool_context(),
            ContextPolicy(
                context_window=model.context_window,
                reserved_output_tokens=model.default_max_output_tokens,
            ),
        )

    async def _build_prompt(
        self,
        prepared: PreparedContext,
        recalled_contexts: list[object],
        tool_observation_refs: tuple[str, ...],
        context: RunContext,
    ) -> tuple[str, str]:
        prompt = prompt_for_agent(self.agent.id)
        model = self.agent.model
        messages: list[dict[str, object]] = []
        if prepared.session.summary:
            messages.append(
                {
                    "role": "system",
                    "content": "Previous session summary:\n" + prepared.session.summary,
                }
            )
        structured = {
            "facts": prepared.session.facts,
            "constraints": prepared.session.constraints,
            "goals": prepared.session.goals,
            "open_tasks": prepared.session.open_tasks,
            "artifacts": prepared.session.artifacts,
        }
        if any(structured.values()):
            messages.append(
                {
                    "role": "system",
                    "content": "Session state:\n"
                    + json.dumps(structured, ensure_ascii=False),
                }
            )
        messages.extend(
            {"role": event.role, "content": event.content}
            for event in prepared.recent_events
        )
        for ref in tool_observation_refs:
            messages.append(
                {
                    "role": "tool",
                    "content": await get_text(
                        self.runtime.persistence.artifact_manager, ref
                    ),
                }
            )
        if recalled_contexts:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "Relevant long-term memory:\n"
                    + json.dumps(
                        recalled_contexts, ensure_ascii=False, default=str
                    ),
                },
            )
        envelope = ModelContextEnvelope(
            messages=messages,
            recalled_memories=recalled_contexts,
            estimated_tokens=prepared.estimated_tokens,
            compacted=prepared.compacted,
        )
        context_pack_ref = await put_model(
            self.runtime.persistence.artifact_manager,
            envelope,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"context:{context.run_id}:{len(prepared.recent_events)}",
        )
        artifact_ref = ArtifactRef(
            artifact_id=context_pack_ref, content_type="application/json"
        )
        request = ModelRequest(
            prompt_ref=prompt.id,
            system_prompt=prompt.render(),
            messages_ref=artifact_ref,
            context_pack_ref=artifact_ref,
            messages=envelope.messages,
            tools=[
                to_model_tool(spec)
                for spec in self.runtime.tool_manager.get_specs(
                    self.agent.allowed_tools
                )
            ],
            max_output_tokens=model.default_max_output_tokens,
        )
        model_request_ref = await put_model(
            self.runtime.persistence.artifact_manager,
            request,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"model-request:{context.run_id}:{len(prepared.recent_events)}",
        )
        return context_pack_ref, model_request_ref
