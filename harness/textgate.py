"""Leaf content-gate helpers shared by persona/memory/agents content layers.
No harness imports — keeps the content-layer modules cycle-free (agents.py needs
these but must not import persona.py, which imports the dispatch chain)."""

from __future__ import annotations

import re

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _meaningful(raw: str) -> bool:
    """True if the file has injectable content — anything but whitespace remains
    after HTML comments are removed. A comment-only template => False (skipped,
    never injected), so shipped templates preserve the byte-identical no-op.
    HTML comments only: '#' is a Markdown heading and must NOT be treated as a
    comment."""
    return bool(_HTML_COMMENT.sub("", raw).strip())


def _trim(text: str, limit: int) -> tuple[str, bool]:
    """Cap text at `limit` chars. Returns (text, was_trimmed)."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True
