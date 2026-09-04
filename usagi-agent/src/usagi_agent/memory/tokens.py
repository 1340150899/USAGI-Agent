"""Tokenizer-independent token budgeting shared by memory and context building."""


def estimate_tokens(text: str) -> int:
    """Conservatively estimate tokens before a provider call."""
    return max(1, (len(text.encode("utf-8")) + 2) // 3)
