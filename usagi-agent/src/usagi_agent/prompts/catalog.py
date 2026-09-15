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
    id="usagi.wechat_material_prompt@1.1.0",
    template=(
        "请严格按顺序处理：\n"
        "1. 先判断当前文字和图片是否足以组成一篇真实、连贯且安全的小红书帖子，禁止补造事实。\n"
        "2. 素材不足时不要调用任何工具，直接说明‘素材不足’以及缺少什么；"
        "这四个字只允许在该场景的 answer 中出现。\n"
        "3. 素材足够时生成完整的中文标题和正文，在 answer 中输出待确认帖子（标题、正文），"
        "询问用户是否发布；这一轮禁止调用 xhs_publish_content。\n"
        "4. 本会话收到的图片按先后顺序编号为第1张、第2张……。每次输出待确认帖子（含按用户意见"
        "修改后的帖子）时，都必须在 answer 末尾单独一行写明选用的图片，"
        "格式为“配图：第1张、第3张”；不配图时写“配图：无”。\n"
        "5. 每个 answer 都必须以“您的小红书运营助手：”开头；这句提示只用于标识消息来源，"
        "不得出现在任何工具参数中，也不得进入发布的标题或正文。\n"
        "6. 用户后续回复会进入同一个会话。只有用户明确同意发布时，才使用上一轮确认稿调用一次 "
        "xhs_publish_content；用户拒绝、修改或表达不明确时不要调用。工具调用后还会由 Runtime "
        "发起独立的工具审批，审批通过前不得声称已经发布。\n"
        "7. 工具审批并执行后，只有明确成功才能说明发布成功；审批拒绝、执行失败或结果不明确时说明未发布。\n\n"
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

STRUCTURED_OUTPUT_PROMPT = PromptSpec(
    id="usagi.structured_output_prompt@1.0.0",
    template=(
        "This session requires a structured final response.\n"
        "When you are ready to finish, call the {terminal_tool_name} tool exactly once.\n"
        "The tool arguments are the final result and must satisfy its JSON Schema.\n"
        "Do not return the final result as plain assistant text or Markdown.\n"
        "If validation fails, correct the arguments and call the tool again.\n"
        "Do not claim acceptance unless the tool result says success."
    ),
)

STRUCTURED_OUTPUT_RETRY_PROMPT = PromptSpec(
    id="usagi.structured_output_retry_prompt@1.0.0",
    template=(
        "This session has a registered output JSON Schema. "
        "A normal assistant response cannot complete the run. "
        "Call {terminal_tool_name} with the final result."
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
    "STRUCTURED_OUTPUT_PROMPT",
    "STRUCTURED_OUTPUT_RETRY_PROMPT",
    "WECHAT_MATERIAL_PROMPT",
    "prompt_for_agent",
]
