"""Minimal model-facing tool declaration and reconciliation records."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

class ToolSpec(BaseModel):
    """Only the information a model needs to choose a function tool."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


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
