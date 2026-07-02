"""Per-session prompt-block hashing (#139 PR2): detect WHICH block of the
system prompt changed between turns. A change at an undeclared moment is a
silent cache invalidator — the cache.boundary trace event makes it visible
in the run trace instead of only in the token bill. Pure functions."""

from __future__ import annotations

import hashlib


def block_hashes(blocks: dict[str, str]) -> dict[str, str]:
    """8-hex-char sha256 per named block. Deterministic, content-only."""
    return {name: hashlib.sha256(text.encode()).hexdigest()[:8]
            for name, text in blocks.items()}


def changed_blocks(old: dict[str, str] | None, new: dict[str, str]) -> list[str]:
    """Sorted names whose hash differs between old and new (added/removed
    count as changed). [] when old is None (first turn: nothing to compare)."""
    if old is None:
        return []
    return sorted(name for name in (old.keys() | new.keys())
                  if old.get(name) != new.get(name))
