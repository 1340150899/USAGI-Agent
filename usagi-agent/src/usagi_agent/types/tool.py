"""Model-facing tool declaration and reconciliation records (design §21.3, §21.6)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from usagi_agent.types.settlement import WriteSafetyMode
from usagi_agent.types.content import ContentPart
from usagi_agent.types.refs import ArtifactRef

ToolErrorCode = Literal[
    "tool.invalid_arguments",  # arguments failed the declared parameter schema
    "tool.parse_error",        # model-emitted arguments were not valid JSON
    "tool.denied",             # scope or policy refused execution
    "tool.timeout",            # execution exceeded timeout_seconds (read class)
    "tool.execution_failed",   # adapter raised or returned an unusable result
    "tool.unknown",            # write-class timeout/crash: result cannot be assumed
]


class ToolSpec(BaseModel):
    """Model-facing declaration plus the execution metadata ToolRuntime needs."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Capability metadata (§21.3): risk drives retry/approval/timeout semantics.
    risk: Literal["read", "write", "high_risk_write"] = "read"
    write_safety: WriteSafetyMode | None = None
    # Execution budgets; enforced by ToolRuntime, not by adapters.
    timeout_seconds: float = 30.0
    max_concurrency: int = 5
    max_retries: int = 0  # read class only
    retry_backoff_seconds: float = 1.0
    max_output_bytes: int = 64_000
    # Extension seams: ecosystem kind, sandbox dispatch, scope checks.
    adapter_kind: Literal["python", "http", "mcp"] = "python"
    execution_env: Literal["in_process", "sandbox"] = "in_process"
    required_scopes: tuple[str, ...] = ()


class ToolAdapterResult(BaseModel):
    """Rich adapter result before ToolManager forms a governed observation."""

    output: dict[str, object] = Field(default_factory=dict)
    content_parts: list[ContentPart] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)


def to_model_tool(spec: ToolSpec) -> dict[str, object]:
    """Convert a declaration to the common function-tool wire shape."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


class ToolReconcileRequest(BaseModel):
    execution_id: str
    operation_id: str
    attempt: int
    generation: int
    tool_name: str


class ReconcileResult(BaseModel):
    status: Literal["success", "failure", "unknown"]
    output: dict[str, object] | None = None
    reason_codes: list[str] = Field(default_factory=list)
