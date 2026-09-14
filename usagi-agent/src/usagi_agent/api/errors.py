"""Framework error hierarchy.

Structured, low-sensitivity errors (no raw exceptions / secrets / unbounded output).
Reason codes come from a closed Registry — never free text that could embed identities.
"""
from __future__ import annotations


class UsagiError(Exception):
    """Base framework error."""

    reason_code: str = "usagi.error"

    def __init__(self, message: str = "", *, reason_code: str | None = None) -> None:
        super().__init__(message or self.reason_code)
        if reason_code is not None:
            self.reason_code = reason_code


class SafeError(UsagiError):
    """Safe, serializable error that may enter ToolObservation/PassResult."""

    reason_code = "usagi.safe_error"


class BudgetExceededError(UsagiError):
    reason_code = "budget.exceeded"


class PolicyDeniedError(UsagiError):
    reason_code = "policy.denied"


class SchemaValidationError(UsagiError):
    reason_code = "schema.invalid"


class FencingGateError(UsagiError):
    """Lease lost / fencing token mismatch — Worker must stop progress writes."""

    reason_code = "fencing.lease_lost"


class IdempotencyConflictError(UsagiError):
    reason_code = "run.idempotency_conflict"


class BundleValidationError(UsagiError):
    reason_code = "bundle.invalid"


class DuplicateAgentError(BundleValidationError):
    reason_code = "agent.duplicate"


class UnknownAgentError(BundleValidationError):
    reason_code = "agent.unknown"


class DuplicateToolError(BundleValidationError):
    reason_code = "tool.duplicate"


class UnknownToolError(BundleValidationError):
    reason_code = "tool.unknown"


class CASMismatch(UsagiError):
    """An optimistic CAS precondition did not match."""

    reason_code = "cas.mismatch"


class LeaseLost(UsagiError):
    """Lease renewal failed — another owner holds the lease."""

    reason_code = "lease.lost"


__all__ = [
    "UsagiError",
    "SafeError",
    "BudgetExceededError",
    "PolicyDeniedError",
    "SchemaValidationError",
    "FencingGateError",
    "IdempotencyConflictError",
    "BundleValidationError",
    "CASMismatch",
    "LeaseLost",
    "DuplicateAgentError",
    "UnknownAgentError",
    "DuplicateToolError",
    "UnknownToolError",
]
