"""Core reference / identity types.

All persistent comparison values use tenant-scoped HMACs or random opaque IDs. Public
references never contain raw content hashes, paths or bearer credentials.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Registry-resolved versioned identifiers (no free text) ---
SchemaRef = str
ReducerRef = str
ConditionRef = str
InvariantRef = str
PromptRef = str
AdapterRef = str
RetrieverRef = str
MemoryPolicyRef = str
PolicyRef = str
ModulePipelineRef = str
AgentPassPipelineRef = str
AgentRef = str
MemoryRef = str


class ArtifactRef(BaseModel):
    """Public, non-bearer reference. Only opaque id, type, non-content lineage id."""

    model_config = ConfigDict(frozen=True)

    artifact_id: str
    content_type: str
    lineage_id: str | None = None


# Semantic Ref aliases — all are public, non-bearer ArtifactRefs. Defined here as
# the single source so modules reference them from `types.refs`.
ContextPackRef = ArtifactRef
AgentActionRef = ArtifactRef
AgentResultRef = ArtifactRef
ToolObservationRef = ArtifactRef
FinalOutputRef = ArtifactRef
PassResultRef = ArtifactRef
ModelResponseRef = ArtifactRef
ModelRequestRef = ArtifactRef
ExecutionContextSnapshotRef = ArtifactRef


class SettlementArtifactRef(ArtifactRef):
    """settlement quarantine receipt: unreadable, short TTL, no business lineage."""


class LineageParent(BaseModel):
    """A parent source of an Artifact, recorded at reserve time."""

    parent_artifact_id: str
    relation: str = "derived_from"


class ArtifactOwner(BaseModel):
    tenant_id: str
    erasure_scope_id: str


class PrincipalRef(BaseModel):
    """Controlled subject reference; plaintext identity lives only in crypto-erasable links."""

    model_config = ConfigDict(frozen=True)

    principal_kind: Literal["user", "service", "system"]
    principal_opaque_id: str


class SecretRef(BaseModel):
    """Credential reference; resolved only at the execution boundary."""

    model_config = ConfigDict(frozen=True)

    secret_id: str
    version: int


class EncryptedField(BaseModel):
    """Field-level ciphertext; plaintext never persisted."""

    ciphertext: str
    key_ref: SecretRef
    algorithm: str


class ThreadControlBinding(BaseModel):
    """Authoritative thread_id <-> control binding.

    v1 single-tenant degenerates to thread_id <-> run_id + fixed tenant; the global
    UNIQUE(thread_id) guard is still implemented so cross-tenant same-thread is rejected.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    thread_id: str
    control_kind: Literal["run", "erasure"]
    control_id: str
    graph_checksum: str
