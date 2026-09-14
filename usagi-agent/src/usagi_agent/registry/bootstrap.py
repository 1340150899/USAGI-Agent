"""Bootstrap settings.

Only deployment values the program genuinely cannot derive are admitted here. Tunable
behavior (retry counts, thresholds, budgets, rule structure, prompt, model selection)
is NOT configurable in the first version — it is implemented directly and pinned by
tests. ``extra="forbid"`` rejects unknown fields at startup (fail-fast).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class BootstrapSettings(BaseModel):
    """Framework-level deployment settings. Business apps layer their own settings on top."""

    model_config = ConfigDict(extra="forbid")

    # --- Identity / tenancy ---
    tenant_id: str = Field(default="default", description="Single-tenant id; never NULL.")
    resume_hmac_key: SecretStr | None = None

    # --- Persistence backend selection ---
    sqlite_path: str | None = Field(
        default=None, description="Path to a single shared SQLite DB (all Stores co-located)."
    )
    sqlite_dev_path: str | None = None
    sqlite_debug_path: str | None = None
    database_environment: Literal["dev", "debug"] = "dev"
    artifact_dir: str | None = Field(default=None, description="Encrypted artifact blob root.")

    # --- Model execution: live is the production path; scripted is deterministic. ---
    model_execution_mode: Literal["live", "scripted"] = "live"

    # --- Observability ---
    service_name: str = "usagi-agent"
    service_version: str = "0.1.0"
    service_instance_id: str = "usagi-agent-1"
    deployment_environment: str = "dev"
    otel_exporter: Literal["none", "console", "otlp"] = "none"
    otel_endpoint: str | None = Field(
        default=None,
        description="OTLP/HTTP base endpoint; setting it selects the OTLP exporter.",
    )
    otel_trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    otel_metric_export_interval_millis: int = Field(default=60_000, ge=1_000)
    log_dir: str | None = Field(
        default=None, description="Root directory for service logs and local span JSONL files."
    )
    span_file_exporter: bool = True

    # --- Encryption / attestation ---
    encryption_kek_ref: str | None = Field(
        default=None, description="KEK SecretRef that wraps per-scope DEKs."
    )
    deployment_attestation_ref: str | None = Field(
        default=None,
        description="Signed DeploymentAttestationRef for integration/real external Gate.",
    )

    def require_durable(self) -> None:
        """Validate SQLite-specific constraints when the durable backend is selected."""
        if not self.selected_sqlite_path():
            raise ValueError("configure sqlite_path or both environment database paths")

    def selected_sqlite_path(self) -> str | None:
        if self.sqlite_dev_path and self.sqlite_debug_path:
            return self.sqlite_debug_path if self.database_environment == "debug" else self.sqlite_dev_path
        return self.sqlite_path

    def all_sqlite_paths(self) -> tuple[str, ...]:
        paths = [path for path in (self.sqlite_dev_path, self.sqlite_debug_path) if path]
        if not paths and self.sqlite_path:
            paths.append(self.sqlite_path)
        return tuple(dict.fromkeys(paths))
