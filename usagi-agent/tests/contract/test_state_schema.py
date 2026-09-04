"""State schema contract tests (design §8.2, §31.1).

The State schema must hold ONLY low-sensitivity routing fields and ArtifactRef (as opaque
id strings). Raw chat, images, prompts, model responses, ContextPack, AgentAction, Tool
params/output and FinalOutput must never enter checkpoint as embedded objects.
"""
from __future__ import annotations

from typing import get_type_hints

from usagi_agent.pipelines.loop.state import AgentRunState
from usagi_agent.pipelines.rules.stage import (
    ContextBuildStagePatch,
    EndStagePatch,
    ModelStagePatch,
    PreRecallStagePatch,
    RecallStagePatch,
    ResultProcessStagePatch,
)
from usagi_agent.types.state import (
    PASS_SCOPED_STATE_FIELDS,
    RUN_SCOPED_STATE_FIELDS,
)


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


def test_run_and_pass_scopes_cover_the_authoritative_state_once():
    fields = set(get_type_hints(AgentRunState))
    assert RUN_SCOPED_STATE_FIELDS.isdisjoint(PASS_SCOPED_STATE_FIELDS)
    assert RUN_SCOPED_STATE_FIELDS | PASS_SCOPED_STATE_FIELDS == fields
    assert not {"recall_bundle_ref", "pass_result_ref", "delegation_count"} & fields


def test_every_stage_patch_is_a_subset_of_agent_run_state():
    state_fields = set(get_type_hints(AgentRunState))
    patches = (
        PreRecallStagePatch,
        RecallStagePatch,
        ContextBuildStagePatch,
        ModelStagePatch,
        ResultProcessStagePatch,
        EndStagePatch,
    )
    for patch in patches:
        assert set(get_type_hints(patch)) <= state_fields
