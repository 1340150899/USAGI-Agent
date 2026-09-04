"""All short-term context and long-term memory behavior lives here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from usagi_agent.memory.store import MemoryStores
from usagi_agent.memory.tokens import estimate_tokens
from usagi_agent.memory.types import (
    ContextPolicy,
    LongTermMemory,
    MemoryExtractionRequest,
    MemoryExtractionTrigger,
    PreparedContext,
    RawEvent,
    SessionContext,
    ToolObservationMemory,
)
from usagi_agent.ports import HealthStatus, MemoryMutationResult, ToolContext
from usagi_agent.types.context import LongTermMemoryCandidate, RecallQuery
from usagi_agent.types.policy import MemoryCandidate
from usagi_agent.types.refs import MemoryRef

class DefaultMemoryManager:
    """Owns raw events, session context, compaction, extraction and recall.

    Pipeline code calls this facade and never implements memory algorithms. Each
    lifecycle has its own BaseStore so raw transcripts cannot accidentally become a
    recall source or be mixed with compacted state.
    """

    def __init__(
        self,
        stores: MemoryStores | None = None,
        *,
        path: str | Path = ".usagi/memory.json",
    ) -> None:
        self.stores = stores or MemoryStores.json_files(path)

    @staticmethod
    def _identity(ctx: ToolContext) -> tuple[str, str]:
        execution = ctx.execution
        return execution.tenant_id, execution.principal.principal_opaque_id

    def _events_ns(self, session_id: str, ctx: ToolContext) -> tuple[str, ...]:
        tenant_id, principal_id = self._identity(ctx)
        return ("raw_conversations", tenant_id, principal_id, session_id)

    def _session_ns(self, session_id: str, ctx: ToolContext) -> tuple[str, ...]:
        tenant_id, principal_id = self._identity(ctx)
        return ("short_term_memory", tenant_id, principal_id, session_id)

    def _memory_ns(self, ctx: ToolContext) -> tuple[str, ...]:
        tenant_id, principal_id = self._identity(ctx)
        return ("long_term_memory", tenant_id, principal_id)

    async def append_event(
        self, *, session_id: str, role: str, content: str, ctx: ToolContext,
        metadata: dict[str, object] | None = None,
    ) -> RawEvent:
        event = RawEvent(
            event_id=f"event_{uuid4().hex}", session_id=session_id,
            role=role, content=content, metadata=metadata or {},  # type: ignore[arg-type]
        )
        await self.stores.raw_conversations.aput(
            self._events_ns(session_id, ctx), event.event_id,
            event.model_dump(mode="json"), index=["content"],
        )
        session = await self.get_session_context(session_id, ctx)
        session.recent_event_ids.append(event.event_id)
        await self.save_session_context(session_id, session, ctx)
        return event
    
    async def list_events(self, session_id: str, ctx: ToolContext) -> list[RawEvent]:
        items = await self.stores.raw_conversations.asearch(
            self._events_ns(session_id, ctx), limit=100_000
        )
        return sorted(
            (RawEvent.model_validate(item.value) for item in items),
            key=lambda event: event.created_at,
        )

    async def get_session_context(self, session_id: str, ctx: ToolContext) -> SessionContext:
        item = await self.stores.short_term.aget(
            self._session_ns(session_id, ctx), "context"
        )
        return SessionContext.model_validate(item.value) if item else SessionContext()

    async def save_session_context(
        self, session_id: str, session: SessionContext, ctx: ToolContext
    ) -> None:
        await self.stores.short_term.aput(
            self._session_ns(session_id, ctx), "context",
            session.model_dump(mode="json"), index=False,
        )

    async def prepare_context(
        self, session_id: str, ctx: ToolContext, policy: ContextPolicy
    ) -> PreparedContext:
        """Build the working set and select a mandatory pre-call compaction range."""
        session = await self.get_session_context(session_id, ctx)
        all_events = await self.list_events(session_id, ctx)
        by_id = {event.event_id: event for event in all_events}
        recent = [by_id[key] for key in session.recent_event_ids if key in by_id]
        structured_state = json.dumps(
            {
                "facts": session.facts, "constraints": session.constraints,
                "goals": session.goals, "open_tasks": session.open_tasks,
                "artifacts": session.artifacts,
            }, ensure_ascii=False,
        )
        structured_tokens = estimate_tokens(structured_state)
        estimated = (
            structured_tokens + estimate_tokens(session.summary)
            + sum(estimate_tokens(e.content) for e in recent)
        )
        threshold = min(
            int(policy.context_window * policy.compression_threshold),
            max(1, policy.context_window - policy.reserved_output_tokens),
        )
        if (
            (not policy.force_compaction and estimated <= threshold)
            or len(recent) < 2
        ):
            return PreparedContext(session=session, recent_events=recent, estimated_tokens=estimated)

        kept: list[RawEvent] = []
        kept_tokens = 0
        for event in reversed(recent):
            size = estimate_tokens(event.content)
            if kept and kept_tokens + size > policy.recent_message_tokens:
                break
            kept.append(event)
            kept_tokens += size
        kept.reverse()
        keep_from = len(recent) - len(kept)
        # Never compact the active user turn. Tool calls/results after it need the
        # original request to remain a valid and meaningful model conversation.
        latest_user = next(
            (
                index
                for index in range(len(recent) - 1, -1, -1)
                if recent[index].role == "user"
            ),
            None,
        )
        if policy.force_compaction:
            # A forced request compacts every eligible older event, regardless of
            # token usage. The active user turn remains available for the next pass.
            keep_from = latest_user if latest_user is not None else len(recent) - 1
        elif latest_user is not None:
            keep_from = min(keep_from, latest_user)
        evicted, kept = recent[:keep_from], recent[keep_from:]
        # A tool result must remain paired with the assistant tool call required by
        # Chat Completions-compatible APIs. Expand the recent boundary when needed.
        if kept and kept[0].role == "tool":
            tool_call_id = kept[0].metadata.get("tool_call_id")
            for index in range(len(evicted) - 1, -1, -1):
                candidate = evicted[index]
                calls = candidate.metadata.get("tool_calls", [])
                if (
                    candidate.role == "assistant"
                    and isinstance(calls, list)
                    and any(
                        isinstance(call, dict)
                        and call.get("tool_call_id") == tool_call_id
                        for call in calls
                    )
                ):
                    kept = recent[index:]
                    evicted = recent[:index]
                    break

        # ContextBuild sends this range to the model. ResultProcess applies the model's
        # structured context update and only then advances the compaction checkpoint.
        return PreparedContext(
            session=session,
            recent_events=kept,
            events_to_compact=evicted,
            estimated_tokens=estimated,
        )

    async def apply_compaction(
        self,
        session_id: str,
        event_ids: list[str],
        ctx: ToolContext,
        *,
        memory_candidates: list[LongTermMemoryCandidate] | None = None,
        **updates: object,
    ) -> SessionContext:
        """Apply a short-term replacement and explicit durable candidates.

        Summary and structured state replace the model-visible working set;
        they are not long-term memories. Only separately selected candidates
        enter the principal-scoped long-term store.
        """
        session = await self.get_session_context(session_id, ctx)
        selected = set(event_ids)
        events = [
            event
            for event in await self.list_events(session_id, ctx)
            if event.event_id in selected
        ]
        for field in ("summary", "facts", "constraints", "goals", "open_tasks", "artifacts"):
            if field in updates and updates[field] is not None:
                setattr(session, field, updates[field])
        session.recent_event_ids = [
            event_id for event_id in session.recent_event_ids if event_id not in selected
        ]
        if events:
            checkpoint = events[-1].event_id
            session.compacted_until = checkpoint
            await self._persist_compaction_memory(
                events, memory_candidates or [], ctx
            )
            session.extracted_until = checkpoint
        await self.save_session_context(session_id, session, ctx)
        return session

    async def _persist_compaction_memory(
        self,
        events: list[RawEvent],
        memory_candidates: list[LongTermMemoryCandidate],
        ctx: ToolContext,
    ) -> None:
        """Persist only LLM-selected cross-session memory candidates.

        Content-derived keys deduplicate durable knowledge across batches and
        sessions; source IDs preserve provenance to the compacted raw range.
        """
        provenance = [event.event_id for event in events]
        for candidate in memory_candidates:
            content = candidate.content.strip()
            if not content:
                continue
            digest = hashlib.sha256(content.encode()).hexdigest()[:24]
            await self.put(
                LongTermMemory(
                    memory_id=f"memory_{uuid4().hex}",
                    namespace="",  # put() normalizes this
                    key=f"compaction-extract:{digest}",
                    content=content,
                    type=candidate.type,
                    confidence=candidate.confidence,
                    importance=candidate.importance,
                    source_event_ids=provenance,
                ),
                ctx,
            )

    async def apply_context_update(
        self, session_id: str, ctx: ToolContext, **updates: object
    ) -> SessionContext:
        """ResultProcess uses this single entry point for structured state updates."""
        session = await self.get_session_context(session_id, ctx)
        for field in ("summary", "facts", "constraints", "goals", "open_tasks", "artifacts"):
            if field in updates and updates[field] is not None:
                setattr(session, field, updates[field])
        await self.save_session_context(session_id, session, ctx)
        return session

    async def get_long_term_memories(
        self, query: RecallQuery, ctx: ToolContext
    ) -> list[LongTermMemory]:
        """Search distilled long-term memories (cross-session, principal-scoped).

        "Recall" is a pipeline-stage concept; the memory domain exposes plain
        getters — the Recall stage's rules call this one.
        """
        hits = await self.stores.long_term.asearch(
            self._memory_ns(ctx), query=query.text, filter={"status": "active"}, limit=10
        )
        return [LongTermMemory.model_validate(hit.value) for hit in hits]

    def _tool_observation_ns(self, ctx: ToolContext) -> tuple[str, ...]:
        tenant_id, principal_id = self._identity(ctx)
        return ("tool_observations", tenant_id, principal_id)

    async def put_tool_observation(
        self, record: ToolObservationMemory, ctx: ToolContext
    ) -> ToolObservationMemory:
        """Persist a tool result candidate; same key supersedes to the freshest."""
        namespace = self._tool_observation_ns(ctx)
        record = record.model_copy(update={"namespace": "/".join(namespace)})
        existing = await self.stores.tool_observations.asearch(
            namespace, filter={"key": record.key, "status": "active"}, limit=100
        )
        for item in existing:
            previous = ToolObservationMemory.model_validate(item.value)
            if previous.content == record.content:
                return previous
            previous.status = "superseded"
            previous.valid_from = record.valid_from
            await self.stores.tool_observations.aput(
                namespace, previous.memory_id, previous.model_dump(mode="json")
            )
        await self.stores.tool_observations.aput(
            namespace, record.memory_id, record.model_dump(mode="json"),
            index=["content", "key"],
        )
        return record

    async def get_tool_observations(
        self, query: RecallQuery, ctx: ToolContext, *, limit: int = 10
    ) -> list[ToolObservationMemory]:
        """Search reusable tool results by relevance (cross-session)."""
        hits = await self.stores.tool_observations.asearch(
            self._tool_observation_ns(ctx),
            query=query.text,
            filter={"status": "active"},
            limit=limit,
        )
        return [ToolObservationMemory.model_validate(hit.value) for hit in hits]

    async def put(self, memory: LongTermMemory, ctx: ToolContext) -> LongTermMemory:
        namespace = self._memory_ns(ctx)
        memory = memory.model_copy(update={"namespace": "/".join(namespace)})
        existing = await self.stores.long_term.asearch(
            namespace, filter={"key": memory.key, "status": "active"}, limit=100
        )
        for item in existing:
            previous = LongTermMemory.model_validate(item.value)
            if previous.content == memory.content:
                return previous
            previous.status = "superseded"
            previous.valid_until = memory.valid_from
            await self.stores.long_term.aput(
                namespace, previous.memory_id, previous.model_dump(mode="json")
            )
        await self.stores.long_term.aput(
            namespace, memory.memory_id, memory.model_dump(mode="json"), index=["content", "key"]
        )
        return memory

    async def extract(
        self, request: MemoryExtractionRequest, ctx: ToolContext
    ) -> list[LongTermMemory]:
        events = await self.list_events(request.session_id, ctx)
        if request.event_ids:
            selected = set(request.event_ids)
            events = [event for event in events if event.event_id in selected]
        elif request.from_event_id or request.to_event_id:
            ids = [event.event_id for event in events]
            start = ids.index(request.from_event_id) if request.from_event_id in ids else 0
            stop = ids.index(request.to_event_id) + 1 if request.to_event_id in ids else len(events)
            events = events[start:stop]
        return await self._extract_events(events, self._memory_ns(ctx), request.trigger)

    async def _extract_events(
        self, events: Iterable[RawEvent], namespace: tuple[str, ...],
        trigger: MemoryExtractionTrigger,
    ) -> list[LongTermMemory]:
        """Incremental extractor + global deduplicating resolver."""
        result: list[LongTermMemory] = []
        for event in events:
            content = event.content.strip()
            if not content or event.role in ("system", "tool"):
                # Tool events are persisted as dedicated tool_observation
                # candidates by ToolExecutionRule; extracting them here too
                # would double-record the same content.
                continue
            key = "event:" + hashlib.sha256(content.encode()).hexdigest()[:24]
            related = await self.stores.long_term.asearch(
                namespace, filter={"key": key, "status": "active"}, limit=1
            )
            if related:
                result.append(LongTermMemory.model_validate(related[0].value))
                continue
            memory = LongTermMemory(
                memory_id=f"memory_{uuid4().hex}", namespace="/".join(namespace),
                key=key, content=content, type="episodic",
                confidence=0.7 if trigger == MemoryExtractionTrigger.CONTEXT_COMPACTION else 0.9,
                source_event_ids=[event.event_id],
            )
            await self.stores.long_term.aput(
                namespace, memory.memory_id, memory.model_dump(mode="json"), index=["content"]
            )
            result.append(memory)
        return result

    async def propose(self, candidate: MemoryCandidate, ctx: ToolContext) -> MemoryMutationResult:
        memory = LongTermMemory(
            memory_id=f"memory_{uuid4().hex}", namespace=candidate.namespace,
            key="candidate:" + hashlib.sha256(str(candidate.content_ref).encode()).hexdigest()[:24],
            content=str(candidate.content_ref),
            type=candidate.type if candidate.type in {"semantic", "episodic", "procedural"} else "semantic",  # type: ignore[arg-type]
            confidence=candidate.confidence, source_event_ids=candidate.evidence_ids,
        )
        memory = await self.put(memory, ctx)
        return MemoryMutationResult(status="applied", memory_ref=MemoryRef(memory.memory_id))

    async def revoke(self, memory_id: str, ctx: ToolContext) -> MemoryMutationResult:
        namespace = self._memory_ns(ctx)
        item = await self.stores.long_term.aget(namespace, memory_id)
        if item is None:
            return MemoryMutationResult(status="unknown")
        memory = LongTermMemory.model_validate(item.value)
        memory.status = "revoked"
        await self.stores.long_term.aput(
            namespace, memory_id, memory.model_dump(mode="json")
        )
        return MemoryMutationResult(status="applied", memory_ref=MemoryRef(memory_id))

    async def health(self) -> HealthStatus:
        try:
            for store in (
                self.stores.raw_conversations,
                self.stores.short_term,
                self.stores.long_term,
                self.stores.tool_observations,
            ):
                await store.alist_namespaces(limit=1)
        except Exception:
            return "unhealthy"
        return "healthy"
