from __future__ import annotations

import json
from typing import TYPE_CHECKING

from usagi_agent.kernel.context import RunContext
from usagi_agent.memory.tokens import estimate_tokens
from usagi_agent.memory.types import ContextPolicy, PreparedContext, RawEvent
from usagi_agent.pipelines.artifacts import (
    ModelContextEnvelope,
    RecallBundle,
    get_model,
    put_model,
)
from usagi_agent.pipelines.config.context_build import ContextBuildPipelineConfig
from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.processors.base import StageProcessor
from usagi_agent.pipelines.rules.stage import ContextBuildStagePatch, RuleExecutionError
from usagi_agent.prompts import CONTEXT_COMPACTION_PROMPT, prompt_for_agent
from usagi_agent.tools import AllowlistSelector, to_model_tool
from usagi_agent.types.content import model_content_from_parts
from usagi_agent.types.model import ModelRequest
from usagi_agent.types.refs import ArtifactRef

if TYPE_CHECKING:
    from usagi_agent.agents.spec import AgentSpec
    from usagi_agent.server.runtime import ServerRuntime


class ContextBuildProcessor(StageProcessor):
    def __init__(
        self,
        config: ContextBuildPipelineConfig,
        runtime: "ServerRuntime",
        agent: "AgentSpec",
    ) -> None:
        super().__init__(runtime, agent)
        self.config = config
        self._tool_selector: AllowlistSelector | None = None

    @property
    def tool_selector(self) -> AllowlistSelector:
        """ToolSelector seam (§21.4): v1 serves the allowlist verbatim; swap
        the implementation to add relevance ranking or token-budget pruning.
        Built lazily so stage construction never touches runtime state."""
        if self._tool_selector is None:
            self._tool_selector = AllowlistSelector(self.runtime.tool_manager)
        return self._tool_selector

    async def process(
        self, state: AgentRunState, context: RunContext
    ) -> ContextBuildStagePatch:
        recalled_contexts = await self._load_recalled_contexts(state)
        recalled_contexts = await self._run_filters(recalled_contexts, context)
        await self._run_rankers(recalled_contexts, context)
        prepared = await self._compress_once(state, context)
        if prepared.requires_compaction:
            context_pack_ref, model_request_ref = await self._build_compaction_prompt(
                prepared, context
            )
            return {
                "context_pack_ref": context_pack_ref,
                "model_request_ref": model_request_ref,
                "context_operation": "compaction",
                "context_compaction_mode": "auto",
            }
        context_pack_ref, model_request_ref = await self._build_prompt(
            prepared,
            recalled_contexts,
            context,
        )
        return {
            "context_pack_ref": context_pack_ref,
            "model_request_ref": model_request_ref,
            "context_operation": "normal",
            "context_compaction_mode": "auto",
        }

    async def _load_recalled_contexts(
        self, state: AgentRunState
    ) -> dict[str, list[object]]:
        """Load every recall source's standard RecallBundle.

        Parsing happens at the Recall stage: every rule emits the same
        bundle shape, so this loader only deserializes and groups — no
        per-source logic. (Bundles are artifacts because graph state holds
        refs only, §8.2.)
        """
        groups: dict[str, list[object]] = {}
        for bundle_ref in (state.get("recall_cache") or {}).values():
            if not bundle_ref:
                continue
            try:
                bundle = await get_model(
                    self.runtime.persistence.artifact_manager, bundle_ref, RecallBundle
                )
            except Exception:
                continue
            if bundle is not None and bundle.hits:
                groups[bundle.source] = list(bundle.hits)
        return groups

    async def _run_filters(
        self, groups: dict[str, list[object]], context: RunContext
    ) -> dict[str, list[object]]:
        result: dict[str, list[object]] = {}
        for key, items in groups.items():
            remaining = items
            for rule in self.config.filters:
                filtered: list[object] = []
                for recalled_context in remaining:
                    outcome = await self.run_rule(
                        "context_build", rule.name,
                        rule.filter_context(recalled_context, self.runtime, context),
                    )
                    if isinstance(outcome, RuleExecutionError):
                        self.raise_on_error(outcome, rule.name)
                    if not isinstance(outcome, bool):
                        raise TypeError("context filter must return bool")
                    if not outcome:
                        filtered.append(recalled_context)
                remaining = filtered
            if remaining:
                result[key] = remaining
        return result

    async def _run_rankers(
        self, groups: dict[str, list[object]], context: RunContext
    ) -> None:
        for items in groups.values():
            for rule in self.config.rankers:
                result = await self.run_rule(
                    "context_build", rule.name,
                    rule.rank_context(items, self.runtime, context),
                )
                if isinstance(result, RuleExecutionError):
                    self.raise_on_error(result, rule.name)
                if result is not None:
                    raise TypeError("context ranker must sort in place and return None")

    async def _compress_once(
        self, state: AgentRunState, context: RunContext
    ) -> PreparedContext:
        model = self.agent.model
        return await self.runtime.memory_manager.prepare_context(
            context.memory_session_id,
            context.to_tool_context(),
            ContextPolicy(
                context_window=model.context_window,
                reserved_output_tokens=model.default_max_output_tokens,
                recent_message_tokens=min(
                    8_000, max(1, model.context_window // 4)
                ),
                force_compaction=(
                    state.get("context_compaction_mode", "auto") == "force"
                ),
            ),
        )

    async def _build_prompt(
        self,
        prepared: PreparedContext,
        recalled_groups: dict[str, list[object]],
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
        messages.extend(self._event_to_message(event) for event in prepared.recent_events)
        grouped_payload = {
            key: items for key, items in recalled_groups.items() if items
        }
        if grouped_payload:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "Recalled context:\n"
                    + json.dumps(grouped_payload, ensure_ascii=False, default=str),
                },
            )
        flat_recalled: list[object] = [
            {"source": key, "item": item}
            for key, items in recalled_groups.items()
            for item in items
        ]
        envelope = ModelContextEnvelope(
            messages=messages,
            recalled_memories=flat_recalled,
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
                for spec in await self.tool_selector.select(
                    self.agent.allowed_tools,
                    token_budget=max(1, model.context_window // 8),
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

    async def _build_compaction_prompt(
        self,
        prepared: PreparedContext,
        context: RunContext,
    ) -> tuple[str, str]:
        events_to_compact = self._fit_compaction_batch(prepared)
        payload = self._compaction_payload(prepared, events_to_compact)
        messages: list[dict[str, object]] = [
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }
        ]
        envelope = ModelContextEnvelope(
            operation="compaction",
            messages=messages,
            compacted_event_ids=[event.event_id for event in events_to_compact],
            estimated_tokens=self._compaction_input_tokens(payload),
        )
        operation_suffix = events_to_compact[-1].event_id
        context_pack_ref = await put_model(
            self.runtime.persistence.artifact_manager,
            envelope,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"context-compaction:{context.run_id}:{operation_suffix}",
        )
        artifact_ref = ArtifactRef(
            artifact_id=context_pack_ref, content_type="application/json"
        )
        request = ModelRequest(
            prompt_ref=CONTEXT_COMPACTION_PROMPT.id,
            system_prompt=CONTEXT_COMPACTION_PROMPT.render(),
            messages_ref=artifact_ref,
            context_pack_ref=artifact_ref,
            messages=messages,
            tools=[],
            max_output_tokens=self.agent.model.default_max_output_tokens,
        )
        model_request_ref = await put_model(
            self.runtime.persistence.artifact_manager,
            request,
            tenant_id=context.tenant_id,
            scope_id=context.run_id,
            operation_id=f"model-request-compaction:{context.run_id}:{operation_suffix}",
        )
        return context_pack_ref, model_request_ref

    @staticmethod
    def _compaction_payload(
        prepared: PreparedContext, events_to_compact: list[RawEvent]
    ) -> dict[str, object]:
        session = prepared.session
        return {
            "previous_context": {
                "summary": session.summary,
                "facts": session.facts,
                "constraints": session.constraints,
                "goals": session.goals,
                "open_tasks": session.open_tasks,
                "artifacts": session.artifacts,
            },
            "events_to_compact": [
                {
                    "event_id": event.event_id,
                    "role": event.role,
                    "content": model_content_from_parts(event.content_parts),
                    "metadata": event.metadata,
                }
                for event in events_to_compact
            ],
        }

    @staticmethod
    def _compaction_input_tokens(payload: dict[str, object]) -> int:
        return (
            estimate_tokens(CONTEXT_COMPACTION_PROMPT.render())
            + estimate_tokens(json.dumps(payload, ensure_ascii=False))
            + 32
        )

    def _fit_compaction_batch(self, prepared: PreparedContext) -> list[RawEvent]:
        """Select the oldest event-boundary prefix that fits one model request."""
        input_budget = (
            self.agent.model.context_window
            - self.agent.model.default_max_output_tokens
        )
        batch: list[RawEvent] = []
        for event in prepared.events_to_compact:
            candidate = [*batch, event]
            payload = self._compaction_payload(prepared, candidate)
            if self._compaction_input_tokens(payload) > input_budget:
                break
            batch = candidate

        if not batch:
            raise RuntimeError(
                "context compaction input exceeds the model window before the "
                "first event; reduce the event size or use a larger-context model"
            )

        # Do not leave a tool result at the head of the next batch. Move its
        # assistant tool-call event with it when a budget boundary splits the pair.
        while (
            len(batch) < len(prepared.events_to_compact)
            and prepared.events_to_compact[len(batch)].role == "tool"
        ):
            batch.pop()
            if not batch:
                raise RuntimeError(
                    "one assistant tool-call group exceeds the compaction input budget"
                )
        return batch

    @staticmethod
    def _event_to_message(event) -> dict[str, object]:
        message: dict[str, object] = {
            "role": event.role,
            "content": model_content_from_parts(event.content_parts),
        }
        if event.role == "assistant":
            calls = event.metadata.get("tool_calls")
            if isinstance(calls, list) and calls:
                message["tool_calls"] = [
                    {
                        "id": str(call.get("tool_call_id", "")),
                        "type": "function",
                        "function": {
                            "name": str(call.get("tool_name", "")),
                            "arguments": json.dumps(
                                call.get("arguments", {}), ensure_ascii=False
                            ),
                        },
                    }
                    for call in calls
                    if isinstance(call, dict)
                ]
        elif event.role == "tool":
            tool_call_id = event.metadata.get("tool_call_id")
            if tool_call_id:
                message["tool_call_id"] = str(tool_call_id)
        return message
