"""Single source of truth for prompt text used by Context Build."""

from usagi_agent.prompts.spec import PromptSpec


RESEARCH_WRITER_PROMPT = PromptSpec(
    id="usagi.research_writer_prompt@1.0.0",
    template=(
        "You are a concise research writer.\n"
        "Return a JSON object with `answer` and optional `context_update`. "
        "context_update may contain summary, facts, constraints, goals, "
        "open_tasks, and artifacts."
    ),
)

CONTEXT_COMPACTION_PROMPT = PromptSpec(
    id="usagi.context_compaction_prompt@1.0.0",
    template=(
        "You compact an agent's session context before its next model call.\n"
        "Return only a JSON object with an empty answer and a required "
        "context_update. Build the new context from the previous summary, "
        "structured session state, and events being compacted. Preserve confirmed "
        "facts, constraints, goals, unfinished tasks, artifact references, decisions, "
        "and information required to continue the task. Remove repetition and obsolete "
        "details. Do not answer the user's request and do not call tools.\n"
        "context_update must contain compacted=true, summary, facts, constraints, "
        "goals, open_tasks, and artifacts."
    ),
)

PROMPTS_BY_AGENT_ID: dict[str, PromptSpec] = {
    "research_writer": RESEARCH_WRITER_PROMPT,
}


def prompt_for_agent(agent_id: str) -> PromptSpec:
    try:
        return PROMPTS_BY_AGENT_ID[agent_id]
    except KeyError as exc:
        raise KeyError(f"no prompt declared for agent: {agent_id}") from exc


__all__ = [
    "CONTEXT_COMPACTION_PROMPT",
    "PROMPTS_BY_AGENT_ID",
    "RESEARCH_WRITER_PROMPT",
    "prompt_for_agent",
]
