"""Shared base types (design §4.1, §8.1).

These types are referenced by multiple modules and are therefore centrally defined to
avoid ambiguity (§4.1). They carry NO execution logic and NO cross-module behavioral
coupling — any module may import from here, and importing here is the *only* permitted
cross-module type sharing besides ``usagi_agent.ports`` and ``usagi_agent.api``.
"""
from usagi_agent.types.refs import (
    AdapterRef,
    ArtifactRef,
    EncryptedField,
    LineageParent,
    MemoryPolicyRef,
    PolicyRef,
    PrincipalRef,
    PromptRef,
    ReducerRef,
    RetrieverRef,
    SchemaRef,
    SecretRef,
    SettlementArtifactRef,
    ThreadControlBinding,
)
from usagi_agent.types.budget import (
    Budget,
    BudgetState,
    BudgetSummary,
    BudgetUsage,
    ContextBudget,
    ContextBudgetUsage,
    PromptBudgetProfile,
    RecallBudget,
)
from usagi_agent.types.content import (
    ContentPart,
    ImageContentPart,
    TextContentPart,
    image_parts_from_content_parts,
    merge_content_parts,
    model_content_from_parts,
    parse_content_parts,
    select_content_parts,
    text_parts_from_content_parts,
    text_from_content_parts,
)

__all__ = [
    "AdapterRef",
    "ArtifactRef",
    "EncryptedField",
    "LineageParent",
    "MemoryPolicyRef",
    "PolicyRef",
    "PrincipalRef",
    "PromptRef",
    "ReducerRef",
    "RetrieverRef",
    "SchemaRef",
    "SecretRef",
    "SettlementArtifactRef",
    "ThreadControlBinding",
    "Budget",
    "BudgetState",
    "BudgetSummary",
    "BudgetUsage",
    "ContextBudget",
    "ContextBudgetUsage",
    "PromptBudgetProfile",
    "RecallBudget",
    "ContentPart",
    "ImageContentPart",
    "TextContentPart",
    "image_parts_from_content_parts",
    "merge_content_parts",
    "model_content_from_parts",
    "parse_content_parts",
    "select_content_parts",
    "text_parts_from_content_parts",
    "text_from_content_parts",
]
