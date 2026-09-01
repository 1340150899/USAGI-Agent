"""FencedCheckpointer + ThreadControlBinding contract tests (design §10.6, §31.1)."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, empty_checkpoint

from usagi_agent.api.errors import FencingGateError
from usagi_agent.persistence.backend import make_run_gate_verifier
from usagi_agent.persistence.inmemory import (
    InMemoryFencedCheckpointer,
    InMemoryRunControlStore,
)
from usagi_agent.persistence.ports.run_lifecycle import RunControlState
from usagi_agent.persistence.sqlite.fenced_checkpointer import SqliteFencedCheckpointer
from usagi_agent.persistence.sqlite.run_control_store import SqliteRunControlStore
from usagi_agent.persistence.sqlite.schema import apply_schema
from usagi_agent.types.budget import BudgetUsage
from usagi_agent.types.settlement import FencingGate


def _gate(run_id: str, token: int, owner: str = "w1") -> FencingGate:
    return FencingGate(tenant_id="default", control_kind="run", control_id=run_id,
                       lease_owner=owner, fencing_token=token)


def _state(run_id="r1", token=5) -> RunControlState:
    return RunControlState(
        run_id=run_id, tenant_id="default", version=0, lease_version=0, run_status="running",
        lease_owner="w1", lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        fencing_token=token, budget_used=BudgetUsage(),
    )


def _checkpoint(value: int) -> Checkpoint:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"]["v"] = value
    return checkpoint


@pytest.mark.asyncio
async def test_inmemory_good_gate_allows_write():
    rc = InMemoryRunControlStore()
    await rc.create(_state())
    cp = InMemoryFencedCheckpointer(gate_verifier=make_run_gate_verifier(rc))
    cfg: RunnableConfig = {
        "configurable": {"thread_id": "r1", "fencing_gate": _gate("r1", 5)}
    }
    await cp.aput(cfg, _checkpoint(1), {"step": 0}, {})
    read_config: RunnableConfig = {"configurable": {"thread_id": "r1"}}
    tup = await cp.aget_tuple(read_config)
    assert tup and tup.checkpoint["channel_values"]["v"] == 1


@pytest.mark.asyncio
async def test_inmemory_bad_token_rejected():
    rc = InMemoryRunControlStore()
    await rc.create(_state(token=5))
    cp = InMemoryFencedCheckpointer(gate_verifier=make_run_gate_verifier(rc))
    cfg: RunnableConfig = {
        "configurable": {"thread_id": "r1", "fencing_gate": _gate("r1", 999)}
    }
    with pytest.raises(FencingGateError):
        await cp.aput(cfg, _checkpoint(2), {"step": 1}, {})


@pytest.mark.asyncio
async def test_sqlite_durable_round_trip_and_bad_gate():
    db = os.path.join(tempfile.gettempdir(), "usagi_contract.sqlite")
    if os.path.exists(db):
        os.remove(db)
    rc = SqliteRunControlStore(db)
    cp = SqliteFencedCheckpointer(db)
    await rc.create(_state("r1", 5))
    cfg: RunnableConfig = {
        "configurable": {
            "thread_id": "r1",
            "tenant_id": "default",
            "fencing_gate": _gate("r1", 5),
        }
    }
    await cp.aput(cfg, _checkpoint(1), {"step": 0}, {})
    read_config: RunnableConfig = {
        "configurable": {"thread_id": "r1", "tenant_id": "default"}
    }
    tup = await cp.aget_tuple(read_config)
    assert tup is not None
    assert tup.checkpoint["channel_values"]["v"] == 1
    bad_config: RunnableConfig = {
        "configurable": {
            "thread_id": "r1",
            "tenant_id": "default",
            "fencing_gate": _gate("r1", 999),
        }
    }
    with pytest.raises(FencingGateError):
        await cp.aput(bad_config, _checkpoint(2), {"step": 1}, {})


def test_thread_control_binding_global_unique():
    """Two tenants inserting the same thread_id must fail the second (§10.6)."""
    db = os.path.join(tempfile.gettempdir(), "usagi_binding.sqlite")
    if os.path.exists(db):
        os.remove(db)
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.commit()
    conn.execute(
        "INSERT INTO thread_control_bindings (tenant_id, thread_id, control_kind, control_id, graph_checksum) "
        "VALUES ('tenantA','t-shared','run','rA','gA')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO thread_control_bindings (tenant_id, thread_id, control_kind, control_id, graph_checksum) "
            "VALUES ('tenantB','t-shared','run','rB','gB')"
        )
        conn.commit()
    conn.close()
