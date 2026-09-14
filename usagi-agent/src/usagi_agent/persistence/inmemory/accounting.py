"""InMemory UsageLedger + AuditStore."""
from __future__ import annotations

import asyncio
from decimal import Decimal

from usagi_agent.persistence.ports.accounting import (
    AuditFact,
    AuditIdentityLink,
    AuditStore,
    AuditWriteDedup,
)
from usagi_agent.types.budget import BudgetUsage
from usagi_agent.types.settlement import UsageFact, UsageIdentityLink


class InMemoryUsageLedger:
    """Append-only facts + crypto-erasable links. Budget projection is rebuilt from links."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._facts: dict[str, UsageFact] = {}
        self._links: dict[str, UsageIdentityLink] = {}
        # run_id -> [usage_event_id]
        self._by_run: dict[str, list[str]] = {}

    async def append(self, fact: UsageFact, link: UsageIdentityLink) -> str:
        async with self._lock:
            if fact.usage_event_id in self._facts:
                return fact.usage_event_id  # idempotent replay
            self._facts[fact.usage_event_id] = fact
            self._links[fact.usage_event_id] = link
            self._by_run.setdefault(link.run_id, []).append(fact.usage_event_id)
            return fact.usage_event_id

    async def get_budget_usage(self, tenant_id: str, run_id: str) -> BudgetUsage:
        async with self._lock:
            ids = self._by_run.get(run_id, [])
            facts = [self._facts[i] for i in ids]
        settled = Decimal("0")
        reserved = Decimal("0")
        in_tok = out_tok = 0
        passes = tool_calls = delegations = 0
        for f in facts:
            if f.kind == "cost":
                if f.phase == "settled":
                    settled += Decimal(str(f.amount))
                elif f.phase == "reserved":
                    reserved += Decimal(str(f.amount))
                elif f.phase == "adjustment":
                    settled += Decimal(str(f.amount))
            elif f.kind == "token":
                if f.unit == "input":
                    in_tok += int(f.amount)
                elif f.unit == "output":
                    out_tok += int(f.amount)
            elif f.kind == "pass" and f.phase in ("settled", "reserved"):
                passes += 1
            elif f.kind == "tool_call" and f.phase in ("settled", "reserved"):
                tool_calls += 1
            elif f.kind == "tool" and f.phase in ("settled", "reserved"):
                # delegation tracking is application-defined; count settled tool delegations.
                delegations += 1
        return BudgetUsage(
            settled_cost=settled,
            reserved_cost=reserved,
            input_tokens=in_tok,
            output_tokens=out_tok,
            passes=passes,
            tool_calls=tool_calls,
            delegations=delegations,
        )


class InMemoryAuditStore(AuditStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._facts: dict[str, AuditFact] = {}
        self._links: dict[str, AuditIdentityLink] = {}
        self._dedup: dict[tuple[str, str], str] = {}  # (tenant, op_key_hmac) -> audit_id

    async def append(
        self, audit_operation_id: str, fact: AuditFact, link: AuditIdentityLink,
    ) -> str:
        async with self._lock:
            key = (link.tenant_id, audit_operation_id)  # simplified: op id acts as dedup key
            if key in self._dedup:
                return self._dedup[key]
            self._facts[fact.audit_id] = fact
            self._links[fact.audit_id] = link
            self._dedup[key] = fact.audit_id
            return fact.audit_id

    async def query(self, tenant_id: str, *, event_type: str | None = None) -> list[AuditFact]:
        async with self._lock:
            out = []
            for aid, fact in self._facts.items():
                link = self._links.get(aid)
                if link is None or link.tenant_id != tenant_id:
                    continue
                if event_type and fact.event_type != event_type:
                    continue
                out.append(fact)
            return out
