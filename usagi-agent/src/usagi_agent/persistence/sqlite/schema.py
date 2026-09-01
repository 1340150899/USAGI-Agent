"""SQLite schema DDL (design §10.6, §24.1, §9 of the application doc).

All root tables carry a non-null ``tenant_id`` participating in unique keys / foreign keys
(§2.3: single-tenant ``default`` still enforces this). The checkpoint tables and run
control share the same DB so ``FencedCheckpointer.put/aput`` can atomically verify the
fencing gate inside the checkpoint write transaction (§10.6).
"""
from __future__ import annotations

SCHEMA = """
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

-- Run start request (§10.5): idempotency dedup before Bundle resolution.
CREATE TABLE IF NOT EXISTS run_start_requests (
    tenant_id                 TEXT NOT NULL,
    idempotency_namespace     TEXT NOT NULL,
    request_idempotency_key   TEXT NOT NULL,
    scenario_key              TEXT NOT NULL,
    client_request_fingerprint TEXT NOT NULL,
    execution_bundle_fingerprint TEXT,
    run_id                    TEXT NOT NULL,
    input_metadata_ref        TEXT,
    status                    TEXT NOT NULL DEFAULT 'pending',
    created_at                TEXT NOT NULL,
    PRIMARY KEY (tenant_id, idempotency_namespace, request_idempotency_key)
);

-- Execution context (immutable, §10.6).
CREATE TABLE IF NOT EXISTS execution_contexts (
    run_id            TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    thread_id         TEXT NOT NULL,
    scenario_key      TEXT NOT NULL,
    original_principal TEXT NOT NULL,
    authorization_scope TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    absolute_deadline TEXT,
    bundle_checksum   TEXT NOT NULL,
    graph_checksum    TEXT NOT NULL,
    application_version TEXT NOT NULL,
    secret_refs       TEXT NOT NULL
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

-- Resume attempts (§10.6).
CREATE TABLE IF NOT EXISTS resume_attempts (
    resume_attempt_id   TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    source_checkpoint_id TEXT NOT NULL,
    source_interrupt_set_digest TEXT,
    validated_payload_ref TEXT,
    auth_link_id        TEXT NOT NULL,
    status              TEXT NOT NULL,
    invocation_generation INTEGER NOT NULL DEFAULT 0,
    fencing_token       INTEGER NOT NULL,
    resulting_checkpoint_id TEXT,
    failure_reason_code TEXT,
    failure_phase       TEXT
);

-- Interrupt credentials (§10.6).
CREATE TABLE IF NOT EXISTS interrupt_credentials (
    credential_id      TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL,
    interrupt_id       TEXT NOT NULL,
    checkpoint_id      TEXT NOT NULL,
    interrupt_set_digest TEXT NOT NULL,
    token_digest       TEXT NOT NULL,
    version             INTEGER NOT NULL,
    status              TEXT NOT NULL,
    delivery_status     TEXT NOT NULL,
    issued_at           TEXT NOT NULL,
    delivered_at        TEXT,
    expires_at          TEXT NOT NULL,
    consumed_by_attempt_id TEXT
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
