"""Pure helpers for the session transcript (no I/O, no agent/model deps).

flatten_agent_messages: collapse a finished agent's message list into one prose
string for the plain-text transcript. The transcript never holds tool/exit roles
or `extra`, so this is the single translation from agent-shape to transcript-shape.
"""

from __future__ import annotations


def flatten_agent_messages(messages: list[dict]) -> str:
    """Join assistant prose (chronological), skip None content, append a
    non-empty terminal submission. Returns "" when nothing usable was produced."""
    parts: list[str] = []
    for m in messages:
        if m.get("role") == "assistant":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        elif m.get("role") == "exit":
            submission = m.get("extra", {}).get("submission")
            if isinstance(submission, str) and submission.strip():
                parts.append(submission.strip())
    return "\n\n".join(parts)


# Cap the triage preamble to the most recent turns. Without a bound the whole
# growing transcript is re-sent to the cheap classifier every turn — cost/latency
# rise with session length and eventually risk the cheap model's context window
# (#256). Triage only needs recent conversational context.
ROUTER_PREAMBLE_MAX_TURNS = 8


def router_preamble(history: list[dict]) -> str:
    """Build a triage preamble from prior USER turns and CHAT assistant answers.
    Excludes agent-origin assistant narration (tool/pytest prose) so triage stays
    clean. Capped to the most recent ROUTER_PREAMBLE_MAX_TURNS lines (tail — newest
    kept). Returns "" for empty history."""
    lines: list[str] = []
    for m in history:
        role, origin = m.get("role"), m.get("origin")
        if role == "user":
            lines.append(f"- user: {m.get('content', '')}")
        elif role == "assistant" and origin == "chat":
            lines.append(f"- assistant: {m.get('content', '')}")
    return "\n".join(lines[-ROUTER_PREAMBLE_MAX_TURNS:])
