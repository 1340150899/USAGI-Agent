"""Recall + Context contract types (design §15.3, §16.2, §17.3).

Shared by PreRecall, RecallSources, ContextBuild and Model rules; defined centrally so
Rule modules don't couple to each other.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from usagi_agent.types.budget import (
    ContextBudget,
    ContextBudgetUsage,
    RecallBudget,
)
from usagi_agent.types.refs import (
    AdapterRef,
    ArtifactRef,
    RetrieverRef,
)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content_ref: ArtifactRef
    name: str | None = None


class AgentRequest(BaseModel):
    """Normalized request entering an Agent Pass."""

    input_ref: ArtifactRef
    conversation: list[Message] = Field(default_factory=list)


class RecallQuery(BaseModel):
    query_id: str
    text: str
    intent: str = "generic"


class MemoryScope(BaseModel):
    namespace: str
    types: list[str] = Field(default_factory=list)


class CachePolicy(BaseModel):
    use_cache: bool = True
    ttl_seconds: int | None = None


class RecallPlan(BaseModel):
    queries: list[RecallQuery] = Field(default_factory=list)
    memory_scopes: list[MemoryScope] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    discover_tools: bool = True
    retriever_refs: list[RetrieverRef] = Field(default_factory=list)
    cache_policy: CachePolicy = Field(default_factory=CachePolicy)
    budget: RecallBudget = Field(default_factory=RecallBudget)


class RecallCandidate(BaseModel):
    source_id: str
    source_type: str
    content_ref: ArtifactRef
    raw_score: float | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None


class SafeSourceError(BaseModel):
    source_id: str
    reason_code: str


class RecallBundle(BaseModel):
    memories: list[ArtifactRef] = Field(default_factory=list)
    knowledge: list[RecallCandidate] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    retrieved_candidates: list[RecallCandidate] = Field(default_factory=list)
    source_errors: list[SafeSourceError] = Field(default_factory=list)


class SourceReference(BaseModel):
    source_id: str
    source_type: str
    span: str | None = None


class CitedMemory(BaseModel):
    memory_id: str
    content_ref: ArtifactRef
    citation_id: str
    score: float = 0.0


class CitedChunk(BaseModel):
    source_id: str
    content_ref: ArtifactRef
    citation_id: str
    score: float = 0.0
    span: str | None = None


class ContextPolicy(BaseModel):
    max_citations: int = 50
    require_citations: bool = True


class ContextBuildInput(BaseModel):
    request: AgentRequest
    recall_bundle: RecallBundle
    tool_observations: list[ArtifactRef] = Field(default_factory=list)
    delegated_results: list[ArtifactRef] = Field(default_factory=list)
    policy: ContextPolicy = Field(default_factory=ContextPolicy)
    budget: ContextBudget | None = None


class ContextPack(BaseModel):
    """Encrypted Artifact content schema (§17.3). State only holds ContextPackRef."""

    current_input: ArtifactRef
    conversation: list[Message] = Field(default_factory=list)
    memories: list[CitedMemory] = Field(default_factory=list)
    knowledge: list[CitedChunk] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    tool_observations: list[ArtifactRef] = Field(default_factory=list)
    delegated_results: list[ArtifactRef] = Field(default_factory=list)
    citations: dict[str, SourceReference] = Field(default_factory=dict)
    budget_usage: ContextBudgetUsage | None = None


class ContextUpdate(BaseModel):
    """Structured model-to-framework state update; answer stays separate."""

    compacted: bool = False
    summary: str | None = None
    facts: dict[str, Any] | None = None
    constraints: list[str] | None = None
    goals: list[str] | None = None
    open_tasks: list[str] | None = None
    artifacts: list[str] | None = None


class LongTermMemoryCandidate(BaseModel):
    """A durable fact or experience explicitly selected by the model."""

    content: str = Field(min_length=1)
    type: Literal["semantic", "episodic", "procedural"] = "semantic"
    confidence: float = Field(default=0.8, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)


class CompactionResult(BaseModel):
    """Separates the short-term replacement from long-term candidates."""

    context_update: ContextUpdate
    long_term_memory_candidates: list[LongTermMemoryCandidate] = Field(
        default_factory=list
    )
