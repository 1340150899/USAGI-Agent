from pathlib import Path

import pytest

from usagi_agent.memory.manager import DefaultMemoryManager
from usagi_agent.memory.store import JsonFileStore
from usagi_agent.memory.types import ContextPolicy
from usagi_agent.ports import GovernedExecutionContext, ToolContext
from usagi_agent.types.context import RecallQuery
from usagi_agent.types.refs import PrincipalRef


def _ctx() -> ToolContext:
    return ToolContext(
        execution=GovernedExecutionContext(
            tenant_id="tenant", principal=PrincipalRef(
                principal_kind="user", principal_opaque_id="user"
            ), authorization_scope=(), control_kind="run", control_id="run",
            fencing_token=1,
        )
    )


@pytest.mark.asyncio
async def test_compaction_keeps_raw_events_and_extracts_incrementally(tmp_path: Path):
    path = tmp_path / "memory.json"
    manager = DefaultMemoryManager(JsonFileStore(path))
    ctx = _ctx()
    for value in ("alpha " * 20, "beta " * 20, "gamma " * 20):
        await manager.append_event(
            session_id="session", role="user", content=value, ctx=ctx
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
        await manager.recall(RecallQuery(query_id="before", text="alpha"), ctx)
    ).hits

    await manager.apply_compaction(
        "session",
        [event.event_id for event in prepared.events_to_compact],
        ctx,
        summary="Earlier messages discussed alpha and beta.",
        facts={"topic": "letters"},
    )

    session = await manager.get_session_context("session", ctx)
    assert session.compacted_until == session.extracted_until
    assert session.summary == "Earlier messages discussed alpha and beta."
    assert session.facts == {"topic": "letters"}
    recalled = await manager.recall(
        RecallQuery(query_id="q", text="alpha"), ctx
    )
    assert recalled.hits

    # A new manager proves the data is in the JSON-backed LangGraph store, not RAM.
    reopened = DefaultMemoryManager(JsonFileStore(path))
    assert (await reopened.get_session_context("session", ctx)).compacted_until
