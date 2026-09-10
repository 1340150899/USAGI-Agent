"""Single source of truth for prompt text used by Context Build."""

from usagi_agent.prompts.spec import PromptSpec

RESEARCH_WRITER_PROMPT = PromptSpec(
    id="usagi.research_writer_prompt@1.1.0",
    template=(
        "You are a concise research writer.\n"
        "Return only one JSON object with `answer` and optional `context_update`. "
        "context_update may contain summary, facts, constraints, goals, "
        "open_tasks, and artifacts. Never return an empty or whitespace-only answer.\n"
        "Example JSON output:\n"
        '{"answer":"Your answer to the user",'
        '"context_update":{"summary":"Optional updated summary"}}'
    ),
)

WECHAT_MATERIAL_PROMPT = PromptSpec(
    id="usagi.wechat_material_prompt@1.0.0",
    template=(
        "请严格按顺序处理：\n"
        "1. 先判断当前文字和图片是否足以组成一篇真实、连贯且安全的小红书帖子，禁止补造事实。\n"
        "2. 素材不足时不要调用任何工具，直接说明‘素材不足’以及缺少什么。\n"
        "3. 素材足够时生成完整的中文标题和正文，在 answer 中输出待确认帖子（标题、正文和图片数量），"
        "询问用户是否发布；这一轮禁止调用 xhs_publish_content。\n"
        "4. 用户后续回复会进入同一个会话。只有用户明确同意发布时，才使用上一轮确认稿调用一次 "
        "xhs_publish_content；用户拒绝、修改或表达不明确时不要调用。工具调用后还会由 Runtime "
        "发起独立的工具审批，审批通过前不得声称已经发布。\n"
        "5. 工具审批并执行后，只有明确成功才能说明发布成功；审批拒绝、执行失败或结果不明确时说明未发布。\n\n"
        "素材文字：\n{material_text}"
    ),
)

CONTEXT_COMPACTION_PROMPT = PromptSpec(
    id="usagi.context_compaction_prompt@1.1.0",
    template=(
        "You compact an agent's session context before its next model call.\n"
        "Return only a JSON object with an empty answer, a required context_update, "
        "and a required long_term_memory_candidates array. Build context_update "
        "from the previous summary, "
        "structured session state, and events being compacted. Preserve confirmed "
        "facts, constraints, goals, unfinished tasks, artifact references, decisions, "
        "and information required to continue the task. Remove repetition and obsolete "
        "details. Do not answer the user's request and do not call tools.\n"
        "context_update must contain compacted=true, summary, facts, constraints, "
        "goals, open_tasks, and artifacts.\n"
        "Separately extract long_term_memory_candidates only from events_to_compact. "
        "Include only information useful beyond this session, such as durable user "
        "preferences, stable facts, reusable decisions, or transferable experience. "
        "Do not copy the session summary, transient progress, tool chatter, or open "
        "tasks merely because they are needed in context_update. Return [] when "
        "nothing deserves long-term retention. Each candidate must contain content, "
        "type (semantic, episodic, or procedural), confidence, and importance."
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
    "WECHAT_MATERIAL_PROMPT",
    "prompt_for_agent",
]
