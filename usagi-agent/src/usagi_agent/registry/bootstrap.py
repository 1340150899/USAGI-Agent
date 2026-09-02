"""Bootstrap settings (design §2.3, §16).

Only deployment values the program genuinely cannot derive are admitted here. Tunable
behavior (retry counts, thresholds, budgets, rule structure, prompt, model selection)
is NOT configurable in the first version — it is implemented directly and pinned by
tests. ``extra="forbid"`` rejects unknown fields at startup (fail-fast).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BootstrapSettings(BaseModel):
    """Framework-level deployment settings. Business apps layer their own settings on top."""

    model_config = ConfigDict(extra="forbid")

    # --- Identity / tenancy (§2.3: single tenant, fixed non-null value) ---
    tenant_id: str = Field(default="default", description="Single-tenant id; never NULL.")

    # --- Persistence backend selection (§24.1: dev=InMemory, durable=SQLite) ---
    persistence_backend: Literal["inmemory", "sqlite"] = "inmemory"
    sqlite_path: str | None = Field(
        default=None, description="Path to a single shared SQLite DB (all Stores co-located)."
    )
    artifact_dir: str | None = Field(default=None, description="Encrypted artifact blob root.")
    memory_path: str = Field(
        default=".usagi/memory.json",
        description="Local LangGraph BaseStore JSON path; replace with a DB store later.",
    )

    # --- Model execution: live is the production path; scripted is deterministic. ---
    model_execution_mode: Literal["live", "scripted"] = "live"

    # --- Observability (§25) ---
    service_name: str = "usagi-agent"
    service_version: str = "0.1.0"
    service_instance_id: str = "usagi-agent-1"
    deployment_environment: str = "dev"
    otel_endpoint: str | None = Field(
        default=None, description="OTLP endpoint. None -> console exporter."
    )

    # --- Encryption / attestation (§24.4, §16) ---
    encryption_kek_ref: str | None = Field(
        default=None, description="KEK SecretRef that wraps per-scope DEKs."
    )
    deployment_attestation_ref: str | None = Field(
        default=None,
        description="Signed DeploymentAttestationRef for integration/real external Gate (§15).",
    )

    def require_durable(self) -> None:
        """Validate SQLite-specific constraints when the durable backend is selected."""
        if self.persistence_backend == "sqlite" and not self.sqlite_path:
            raise ValueError("sqlite_path is required when persistence_backend='sqlite'")
