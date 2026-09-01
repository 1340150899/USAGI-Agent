"""State schema contract tests (design §8.2, §31.1).

The State schema must hold ONLY low-sensitivity routing fields and ArtifactRef (as opaque
id strings). Raw chat, images, prompts, model responses, ContextPack, AgentAction, Tool
params/output and FinalOutput must never enter checkpoint as embedded objects.
"""
from __future__ import annotations

from typing import get_type_hints

from usagi_agent.pipelines.loop.state import AgentRunState


def test_state_schema_holds_only_low_sensitivity_fields():
    """Every AgentRunState field must be str / int / list[str] / dict — never a domain BaseModel."""
    hints = get_type_hints(AgentRunState)
    allowed_prefixes = ("str", "int", "list", "dict", "Annotated")
    for field, anno in hints.items():
        anno_str = str(anno)
        assert any(anno_str.startswith(p) or f"'{p}" in anno_str or f"[{p}" in anno_str
                   for p in allowed_prefixes) or "TypedDict" in anno_str, (
            f"field {field!r} has disallowed type {anno_str}: must be low-sensitivity"
        )
