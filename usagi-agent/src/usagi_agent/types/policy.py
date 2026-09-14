"""Policy / Guardrail / Memory-candidate contract types."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from usagi_agent.types.refs import ArtifactRef


class PolicyObligation(BaseModel):
    kind: str
    detail_ref: ArtifactRef | None = None


class PolicyDecision(BaseModel):
    """Deterministic, versioned; LLM cannot override."""

    effect: Literal["allow", "deny", "require_approval"]
    reason_codes: list[str] = Field(default_factory=list)
    obligations: list[PolicyObligation] = Field(default_factory=list)


class GuardrailResult(BaseModel):
    passed: bool
    reason_codes: list[str] = Field(default_factory=list)
    sanitized_ref: ArtifactRef | None = None


class MemoryCandidate(BaseModel):
    content_ref: ArtifactRef
    type: str
    namespace: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
