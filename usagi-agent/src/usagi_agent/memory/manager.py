"""All short-term context and long-term memory behavior lives here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from langgraph.store.base import BaseStore

from usagi_agent.memory.store import JsonFileStore
from usagi_agent.memory.types import (
    ContextPolicy,
    LongTermMemory,
    MemoryExtractionRequest,
    MemoryExtractionTrigger,
    PreparedContext,
    RawEvent,
    SessionContext,
)
from usagi_agent.ports import HealthStatus, MemoryMutationResult, MemoryRecallResult, ToolContext
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.policy import MemoryCandidate
from usagi_agent.types.refs import MemoryRef


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-independent pre-call estimate."""
    return max(1, (len(text.encode("utf-8")) + 2) // 3)


def _fit_recent_text(text: str, token_budget: int) -> str:
    """Bound fallback summaries while favoring the most recently compacted facts."""
    if token_budget <= 0:
        return ""
    while text and estimate_tokens(text) > token_budget:
        text = text[max(1, len(text) // 10) :]
    return text


class DefaultMemoryManager:
    """Owns raw events, session context, compaction, extraction and recall.

    Pipeline code calls this facade and never implements memory algorithms. The injected
    store is LangGraph's BaseStore; V1 defaults to a local JSON implementation.
    """

    def __init__(self, store: BaseStore | None = None, *, path: str | Path = ".usagi/memory.json") -> None:
        self.store = store or JsonFileStore(path)

    @staticmethod
    def _identity(ctx: ToolContext) -> tuple[str, str]:
        execution = ctx.execution
        return execution.tenant_id, execution.principal.principal_opaque_id

    def _events_ns(self, session_id: str, ctx: ToolContext) -> tuple[str, ...]:
        return ("events", self._identity(ctx)[0], session_id)

    def _session_ns(self, session_id: str, ctx: ToolContext) -> tuple[str, ...]:
        return ("sessions", self._identity(ctx)[0], session_id)

    def _memory_ns(self, ctx: ToolContext) -> tuple[str, ...]:
        tenant_id, principal_id = self._identity(ctx)
        return ("memories", tenant_id, principal_id)

    async def append_event(
        self, *, session_id: str, role: str, content: str, ctx: ToolContext,
        metadata: dict[str, object] | None = None,
    ) -> RawEvent:
        event = RawEvent(
            event_id=f"event_{uuid4().hex}", session_id=session_id,
            role=role, content=content, metadata=metadata or {},  # type: ignore[arg-type]
        )
        await self.store.aput(
            self._events_ns(session_id, ctx), event.event_id,
            event.model_dump(mode="json"), index=["content"],
        )
        session = await self.get_session_context(session_id, ctx)
        session.recent_event_ids.append(event.event_id)
        await self.save_session_context(session_id, session, ctx)
        return event
    
    async def list_events(self, session_id: str, ctx: ToolContext) -> list[RawEvent]:
        items = await self.store.asearch(self._events_ns(session_id, ctx), limit=100_000)
        return sorted(
            (RawEvent.model_validate(item.value) for item in items),
            key=lambda event: event.created_at,
        )

    async def get_session_context(self, session_id: str, ctx: ToolContext) -> SessionContext:
        item = await self.store.aget(self._session_ns(session_id, ctx), "context")
        return SessionContext.model_validate(item.value) if item else SessionContext()

    async def save_session_context(
        self, session_id: str, session: SessionContext, ctx: ToolContext
    ) -> None:
        await self.store.aput(
            self._session_ns(session_id, ctx), "context",
            session.model_dump(mode="json"), index=False,
        )

    async def prepare_context(
        self, session_id: str, ctx: ToolContext, policy: ContextPolicy
    ) -> PreparedContext:
        """Build the working set and perform mandatory pre-call compaction."""
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
        if estimated <= threshold or len(recent) < 2:
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
        evicted = recent[: len(recent) - len(kept)]
        if not evicted:
            evicted, kept = recent[:-1], recent[-1:]

        # Raw events are never deleted. V1's lossless labelled summary is replaceable by
        # an LLM compactor without changing the pipeline contract.
        addition = "\n".join(f"{event.role}: {event.content}" for event in evicted)
        combined_summary = "\n".join(part for part in (session.summary, addition) if part)
        summary_budget = max(1, threshold - structured_tokens - kept_tokens)
        session.summary = _fit_recent_text(combined_summary, summary_budget)
        session.recent_event_ids = [event.event_id for event in kept]
        session.compacted_until = evicted[-1].event_id
        await self._extract_events(evicted, self._memory_ns(ctx), MemoryExtractionTrigger.CONTEXT_COMPACTION)
        session.extracted_until = evicted[-1].event_id
        await self.save_session_context(session_id, session, ctx)
        return PreparedContext(
            session=session, recent_events=kept,
            estimated_tokens=structured_tokens + estimate_tokens(session.summary) + kept_tokens,
            compacted=True,
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

    async def recall(self, query: RecallQuery, ctx: ToolContext) -> MemoryRecallResult:
        hits = await self.store.asearch(
            self._memory_ns(ctx), query=query.text, filter={"status": "active"}, limit=10
        )
        return MemoryRecallResult(
            hits=[LongTermMemory.model_validate(hit.value) for hit in hits], reason_codes=[]
        )

    async def put(self, memory: LongTermMemory, ctx: ToolContext) -> LongTermMemory:
        namespace = self._memory_ns(ctx)
        existing = await self.store.asearch(
            namespace, filter={"key": memory.key, "status": "active"}, limit=100
        )
        for item in existing:
            previous = LongTermMemory.model_validate(item.value)
            if previous.content == memory.content:
                return previous
            previous.status = "superseded"
            previous.valid_until = memory.valid_from
            await self.store.aput(namespace, previous.memory_id, previous.model_dump(mode="json"))
        await self.store.aput(
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
            if not content or event.role == "system":
                continue
            key = "event:" + hashlib.sha256(content.encode()).hexdigest()[:24]
            related = await self.store.asearch(
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
            await self.store.aput(
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
        item = await self.store.aget(namespace, memory_id)
        if item is None:
            return MemoryMutationResult(status="unknown")
        memory = LongTermMemory.model_validate(item.value)
        memory.status = "revoked"
        await self.store.aput(namespace, memory_id, memory.model_dump(mode="json"))
        return MemoryMutationResult(status="applied", memory_ref=MemoryRef(memory_id))

    async def health(self) -> HealthStatus:
        try:
            await self.store.alist_namespaces(limit=1)
        except Exception:
            return "unhealthy"
        return "healthy"
