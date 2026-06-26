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
