import sqlite3
from pathlib import Path

import pytest

from usagi_agent.memory.manager import DefaultMemoryManager
from usagi_agent.memory.store import MemoryStores
from usagi_agent.memory.types import ContextPolicy
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.types.context import LongTermMemoryCandidate, RecallQuery
from usagi_agent.types.content import TextContentPart
from usagi_agent.types.refs import PrincipalRef


def _ctx(principal: str = "user") -> ToolContext:
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id="tenant", principal=PrincipalRef(
                principal_kind="user", principal_opaque_id=principal
            ), authorization_scope=(), control_kind="run", control_id="run",
            fencing_token=1,
        )
    )


@pytest.mark.asyncio
async def test_compaction_keeps_raw_events_and_extracts_incrementally(tmp_path: Path):
    path = tmp_path / "memory.db"
    manager = DefaultMemoryManager(MemoryStores.sqlite(path))
    ctx = _ctx()
    for value in ("alpha " * 20, "beta " * 20, "gamma " * 20):
        await manager.append_event(
            session_id="session", role="user",
            content_parts=[TextContentPart(text=value)], ctx=ctx
        )

    prepared = await manager.prepare_context(
        "session", ctx,
        ContextPolicy(
            context_window=200, compression_threshold=0.5,
            reserved_output_tokens=10, recent_message_tokens=20,
        ),
    )

    assert prepared.requires_compaction
    assert not prepared.compacted
    assert len(await manager.list_events("session", ctx)) == 3
    assert prepared.session.compacted_until is None
    assert not (
        await manager.get_long_term_memories(RecallQuery(query_id="before", text="alpha"), ctx)
    )

    await manager.apply_compaction(
        "session",
        [event.event_id for event in prepared.events_to_compact],
        ctx,
        summary="Earlier messages discussed alpha and beta.",
        facts={"topic": "letters"},
        memory_candidates=[
            LongTermMemoryCandidate(
                content="The user prefers letter-based examples.",
                type="semantic",
                confidence=0.9,
                importance=0.8,
            )
        ],
    )

    session = await manager.get_session_context("session", ctx)
    assert session.compacted_until == session.extracted_until
    assert session.summary == "Earlier messages discussed alpha and beta."
    assert session.facts == {"topic": "letters"}
    recalled = await manager.get_long_term_memories(
        RecallQuery(query_id="q", text="letter-based examples"), ctx
    )
    assert recalled
    # Only the explicit durable candidate is persisted. Neither the short-term
    # summary nor verbatim raw events are promoted implicitly.
    assert all(
        hit.type == "semantic" and hit.key.startswith("compaction-extract:")
        for hit in recalled
    )
    assert any("prefers letter-based examples" in hit.content for hit in recalled)
    assert not await manager.get_long_term_memories(
        RecallQuery(query_id="summary", text="alpha and beta"), ctx
    )
    assert not any(hit.content.startswith("alpha alpha") for hit in recalled)

    # A new manager proves the data is persisted in SQLite, not process memory.
    reopened = DefaultMemoryManager(MemoryStores.sqlite(path))
    assert (await reopened.get_session_context("session", ctx)).compacted_until

    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM short_term_memory"
        ).fetchone()[0]
        assert database.execute(
            "SELECT COUNT(*) FROM long_term_memory"
        ).fetchone()[0]


@pytest.mark.asyncio
async def test_append_event_operation_id_is_stable_on_replay(tmp_path: Path):
    manager = DefaultMemoryManager(MemoryStores.sqlite(tmp_path / "memory.db"))
    ctx = _ctx()

    first = await manager.append_event(
        session_id="session",
        role="user",
        content_parts=[TextContentPart(text="hello")],
        ctx=ctx,
        operation_id="memory:user-event:run",
    )
    replayed = await manager.append_event(
        session_id="session",
        role="user",
        content_parts=[TextContentPart(text="hello")],
        ctx=ctx,
        operation_id="memory:user-event:run",
    )

    assert replayed.event_id == first.event_id
    assert len(await manager.list_events("session", ctx)) == 1


@pytest.mark.asyncio
async def test_weixin_uid_with_period_is_valid_memory_identity(tmp_path: Path):
    manager = DefaultMemoryManager(MemoryStores.sqlite(tmp_path / "memory.db"))
    ctx = _ctx("user@im.wechat")

    await manager.append_event(
        session_id="session.with.period",
        role="user",
        content_parts=[TextContentPart(text="hello")],
        ctx=ctx,
    )

    assert [
        event.search_text
        for event in await manager.list_events("session.with.period", ctx)
    ] == ["hello"]


@pytest.mark.asyncio
async def test_raw_and_short_term_are_principal_and_session_isolated(tmp_path: Path):
    manager = DefaultMemoryManager(MemoryStores.sqlite(tmp_path / "memory.db"))
    alice = _ctx("alice")
    bob = _ctx("bob")

    alice_event = await manager.append_event(
        session_id="same-id", role="user",
        content_parts=[TextContentPart(text="alice private")], ctx=alice
    )
    await manager.append_event(
        session_id="other-session", role="user",
        content_parts=[TextContentPart(text="alice other")], ctx=alice
    )
    await manager.append_event(
        session_id="same-id", role="user",
        content_parts=[TextContentPart(text="bob private")], ctx=bob
    )

    assert [event.search_text for event in await manager.list_events("same-id", alice)] == [
        "alice private"
    ]
    assert [event.search_text for event in await manager.list_events("same-id", bob)] == [
        "bob private"
    ]
    assert [
        event.search_text for event in await manager.list_events("other-session", alice)
    ] == ["alice other"]
    assert (
        await manager.get_session_context("same-id", alice)
    ).recent_event_ids == [alice_event.event_id]


@pytest.mark.asyncio
async def test_short_term_summary_is_not_implicitly_promoted(tmp_path: Path):
    manager = DefaultMemoryManager(MemoryStores.sqlite(tmp_path / "memory.db"))
    ctx = _ctx()
    old = await manager.append_event(
        session_id="session", role="user",
        content_parts=[TextContentPart(text="temporary alpha progress")], ctx=ctx
    )
    await manager.append_event(
        session_id="session", role="user",
        content_parts=[TextContentPart(text="current request")], ctx=ctx
    )

    await manager.apply_compaction(
        "session",
        [old.event_id],
        ctx,
        summary="Temporary alpha progress is summarized for this session.",
        memory_candidates=[],
    )

    assert "alpha progress" in (
        await manager.get_session_context("session", ctx)
    ).summary
    assert not await manager.get_long_term_memories(
        RecallQuery(query_id="q", text="alpha progress"), ctx
    )
