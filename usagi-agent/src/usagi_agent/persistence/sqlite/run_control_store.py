"""SQLite RunControlStore (design §10.6).

Shares the same DB as :class:`SqliteFencedCheckpointer` so the gate verification the
checkpointer performs reads authoritative rows written here. Ordinary ``version`` CAS is
separate from ``lease_version`` CAS.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from usagi_agent.api.errors import CASMismatch
from usagi_agent.persistence.ports.run_lifecycle import RunControlState
from usagi_agent.types.budget import BudgetUsage


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class SqliteRunControlStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self):
        import aiosqlite

        return aiosqlite.connect(self._db_path)

    async def get(self, run_id: str) -> RunControlState | None:
        async with self._connect() as conn:
            cur = await conn.execute("SELECT * FROM run_controls WHERE run_id = ?", (run_id,))
            row = await cur.fetchone()
            await cur.close()
        if row is None:
            return None
        return self._row_to_state(row)

    async def create(self, state: RunControlState) -> RunControlState:
        async with self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cols = (
                "run_id, tenant_id, version, lease_version, run_status, "
                "suspended_checkpoint_id, interrupt_set_digest, accepted_resume_attempt_id, budget_used, "
                "cancel_requested_at, cancelled_at, cancellation_reason_code, cancellation_detail_ref, "
                "lease_owner, lease_expires_at, fencing_token, final_result_ref"
            )
            placeholders = ",".join(["?"] * 17)
            try:
                await conn.execute(
                    f"INSERT INTO run_controls ({cols}) VALUES ({placeholders})",
                    self._state_to_args(state),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise CASMismatch(f"run_control {state.run_id} already exists")
        return state

    async def cas_update(self, run_id: str, expected_version: int, new_state: RunControlState) -> RunControlState:
        async with self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT version FROM run_controls WHERE run_id = ?", (run_id,)
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None or row[0] != expected_version:
                await conn.rollback()
                raise CASMismatch(f"cas_update {run_id}: expected version {expected_version}")
            bumped = new_state.model_copy(update={"version": expected_version + 1})
            await conn.execute(
                "UPDATE run_controls SET version=?, run_status=?, suspended_checkpoint_id=?, "
                "interrupt_set_digest=?, accepted_resume_attempt_id=?, budget_used=?, "
                "cancel_requested_at=?, cancelled_at=?, cancellation_reason_code=?, "
                "cancellation_detail_ref=?, final_result_ref=? WHERE run_id=?",
                (bumped.version, bumped.run_status, bumped.suspended_checkpoint_id,
                 bumped.interrupt_set_digest, bumped.accepted_resume_attempt_id,
                 bumped.budget_used.model_dump_json(), _iso(bumped.cancel_requested_at),
                 _iso(bumped.cancelled_at),
                 bumped.cancellation_reason_code.value if bumped.cancellation_reason_code else None,
                 bumped.cancellation_detail_ref.artifact_id if bumped.cancellation_detail_ref else None,
                 bumped.final_result_ref.model_dump_json() if bumped.final_result_ref else None, run_id),
            )
            await conn.commit()
        return bumped

    async def cas_lease(self, run_id: str, *, expected_lease_version: int, lease_owner, lease_expires_at, fencing_token) -> RunControlState:
        async with self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT lease_version, run_status, lease_expires_at FROM run_controls WHERE run_id = ?",
                (run_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None or row[0] != expected_lease_version:
                await conn.rollback()
                raise CASMismatch(f"cas_lease {run_id}: expected lease_version {expected_lease_version}")
            # Only allow lease changes on running/resume_accepted (fencing gate semantics).
            if row[1] not in ("running", "resume_accepted") and not (row[1]=="cancel_requested" and lease_owner is None):
                await conn.rollback()
                raise CASMismatch(f"cas_lease {run_id}: run_status {row[1]} not leaseable")
            await conn.execute(
                "UPDATE run_controls SET lease_version=?, lease_owner=?, lease_expires_at=?, fencing_token=? WHERE run_id=?",
                (expected_lease_version + 1, lease_owner, _iso(lease_expires_at), fencing_token, run_id),
            )
            await conn.commit()
        return await self.get(run_id)  # type: ignore[return-value]

    @staticmethod
    def _state_to_args(state: RunControlState) -> tuple:
        return (
            state.run_id, state.tenant_id, state.version, state.lease_version, state.run_status,
            state.suspended_checkpoint_id, state.interrupt_set_digest, state.accepted_resume_attempt_id,
            state.budget_used.model_dump_json(),
            _iso(state.cancel_requested_at), _iso(state.cancelled_at),
            state.cancellation_reason_code.value if state.cancellation_reason_code else None,
            state.cancellation_detail_ref.artifact_id if state.cancellation_detail_ref else None,
            state.lease_owner, _iso(state.lease_expires_at), state.fencing_token,
            state.final_result_ref.model_dump_json() if state.final_result_ref else None,
        )

    @staticmethod
    def _row_to_state(row: Any) -> RunControlState:
        from usagi_agent.types.run import CancellationReasonCode
        from usagi_agent.types.refs import ArtifactRef

        cols = [
            "run_id", "tenant_id", "version", "lease_version", "run_status",
            "suspended_checkpoint_id", "interrupt_set_digest", "accepted_resume_attempt_id",
            "budget_used", "cancel_requested_at", "cancelled_at", "cancellation_reason_code",
            "cancellation_detail_ref", "lease_owner", "lease_expires_at", "fencing_token", "final_result_ref",
        ]
        d = dict(zip(cols, row))
        return RunControlState(
            run_id=d["run_id"], tenant_id=d["tenant_id"], version=d["version"],
            lease_version=d["lease_version"], run_status=d["run_status"],
            suspended_checkpoint_id=d["suspended_checkpoint_id"],
            interrupt_set_digest=d["interrupt_set_digest"],
            accepted_resume_attempt_id=d["accepted_resume_attempt_id"],
            budget_used=BudgetUsage.model_validate_json(d["budget_used"]),
            cancel_requested_at=datetime.fromisoformat(d["cancel_requested_at"]) if d["cancel_requested_at"] else None,
            cancelled_at=datetime.fromisoformat(d["cancelled_at"]) if d["cancelled_at"] else None,
            cancellation_reason_code=CancellationReasonCode(d["cancellation_reason_code"]) if d["cancellation_reason_code"] else None,
            final_result_ref=ArtifactRef.model_validate_json(d["final_result_ref"]) if d.get("final_result_ref") else None,
            fencing_token=d["fencing_token"],
            lease_owner=d["lease_owner"],
            lease_expires_at=datetime.fromisoformat(d["lease_expires_at"]) if d["lease_expires_at"] else None,
        )
