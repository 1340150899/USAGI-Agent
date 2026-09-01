"""Budget types (design §4.1).

Note (§4.1): pass/tool-call/delegation count limits are NOT declared here — they are
required direct fields on ``AgentLoopSpec`` (§9.3) to avoid a second source of truth.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    """Hard budget ceiling, frozen onto AgentLoopSpec/TeamSpec/Task; not Run-overridable."""

    model_config = ConfigDict(frozen=True)

    max_total_cost: Decimal | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


class BudgetUsage(BaseModel):
    """UsageLedger-aggregated usage projection, written to RunControlState.budget_used."""

    settled_cost: Decimal = Decimal("0")
    reserved_cost: Decimal = Decimal("0")
    input_tokens: int = 0
    output_tokens: int = 0
    passes: int = 0
    tool_calls: int = 0
    delegations: int = 0


class BudgetSummary(BudgetUsage):
    """Checkpoint snapshot for routing/display; cannot back-write RunControl.budget_used."""

    remaining: Budget | None = None


class BudgetState(BaseModel):
    """Remaining-budget view input to PreRecallRule."""

    total: Budget
    used: BudgetUsage
    remaining: Budget


class RecallBudget(BaseModel):
    """Budget allocated to the recall phase inside a RecallPlan."""

    max_candidates: int | None = None
    max_tokens: int | None = None
    parallel_sources: int = 1


class ContextBudget(BaseModel):
    """Token budget for ContextBuildRule, computed by AgentManager (§17.3)."""

    total_tokens: int
    system_prompt_tokens: int
    tool_schema_tokens: int
    max_output_tokens: int
    reserved_for_citations: int = 0


class ContextBudgetUsage(BaseModel):
    """Actual per-partition token occupancy; compared against ContextBudget reservations."""

    system_prompt_used: int
    tool_schema_used: int
    conversation_used: int
    memories_used: int
    knowledge_used: int
    tool_observations_used: int
    total_used: int


class PromptBudgetProfile(BaseModel):
    """Prompt budget attributes (§18.4) used to compute ContextBudget reservations."""

    system_prompt_tokens: int
    reserved_output_tokens: int
