"""SQLite schema DDL (design §10.6, §24.1, §9 of the application doc).

Run-control and checkpoint tables share one database so fenced writes can validate the
authoritative thread binding, lease owner and fencing token in the same transaction.
"""
from __future__ import annotations

_REMOVED_EMPTY_TABLES = (
    "run_start_requests",
    "execution_contexts",
    "resume_attempts",
    "interrupt_credentials",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_states (
    name TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    uid      TEXT NOT NULL UNIQUE,
    uptime   TEXT NOT NULL,
    crtime   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id       TEXT PRIMARY KEY,
    uid      TEXT NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('idle','running','pending_approval','closed')),
    uptime   TEXT NOT NULL,
    crtime   TEXT NOT NULL,
    FOREIGN KEY (uid) REFERENCES users(uid)
);
CREATE TABLE IF NOT EXISTS short_term_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    text        TEXT NOT NULL,
    type        TEXT NOT NULL,
    is_compress INTEGER NOT NULL DEFAULT 0,
    uptime      TEXT NOT NULL,
    crtime      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_short_term_session ON short_term_memory(session_id,id);

CREATE TABLE IF NOT EXISTS long_term_memory (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    uid     TEXT NOT NULL,
    text    TEXT NOT NULL,
    type    TEXT,
    uptime  TEXT NOT NULL,
    crtime  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_long_term_uid ON long_term_memory(uid,id);

CREATE TABLE IF NOT EXISTS tool_observation_memory (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    uid     TEXT NOT NULL,
    text    TEXT NOT NULL,
    type    TEXT,
    uptime  TEXT NOT NULL,
    crtime  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tool_memory_uid ON tool_observation_memory(uid,id);

CREATE TABLE IF NOT EXISTS artifact_blobs (
    key TEXT PRIMARY KEY,
    payload BLOB NOT NULL
);

-- ThreadControlBinding (§10.6): globally unique thread_id; cross-tenant same thread rejected.
CREATE TABLE IF NOT EXISTS thread_control_bindings (
    tenant_id      TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    control_kind   TEXT NOT NULL CHECK (control_kind IN ('run','erasure')),
    control_id     TEXT NOT NULL,
    graph_checksum TEXT NOT NULL,
    PRIMARY KEY (tenant_id, thread_id),
    UNIQUE (thread_id)
);

-- Run control (§10.6): ordinary `version` vs independent `lease_version`.
CREATE TABLE IF NOT EXISTS run_controls (
    run_id                       TEXT PRIMARY KEY,
    tenant_id                    TEXT NOT NULL,
    version                      INTEGER NOT NULL,
    lease_version                INTEGER NOT NULL,
    run_status                   TEXT NOT NULL,
    suspended_checkpoint_id      TEXT,
    interrupt_set_digest         TEXT,
    accepted_resume_attempt_id   TEXT,
    budget_used                  TEXT NOT NULL,
    cancel_requested_at          TEXT,
    cancelled_at                 TEXT,
    cancellation_reason_code     TEXT,
    cancellation_detail_ref      TEXT,
    lease_owner                  TEXT,
    lease_expires_at             TEXT,
    fencing_token                INTEGER NOT NULL
);

-- Checkpoints + pending writes (LangGraph contract; fenced on write).
CREATE TABLE IF NOT EXISTS checkpoints (
    tenant_id      TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    checkpoint_id  TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    checkpoint     BLOB NOT NULL,
    metadata       BLOB NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, thread_id, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS ix_checkpoints_thread ON checkpoints(tenant_id, thread_id, created_at);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    tenant_id      TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    checkpoint_id  TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    task_path      TEXT NOT NULL DEFAULT '',
    channel        TEXT NOT NULL,
    value          BLOB,
    idx            INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, thread_id, checkpoint_id, task_id, task_path, idx)
);
"""


def apply_schema(conn) -> None:
    """Execute the full DDL on a sqlite3/aiosqlite connection."""
    conn.executescript(SCHEMA)
    for table in _REMOVED_EMPTY_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise RuntimeError(
                f"legacy table {table!r} contains data; migrate it before upgrading"
            )
        conn.execute(f"DROP TABLE {table}")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(run_controls)")}
    if "final_result_ref" not in columns:
        conn.execute("ALTER TABLE run_controls ADD COLUMN final_result_ref TEXT")
