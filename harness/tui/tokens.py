"""Shared, pure display vocabulary for the TUI design system: the glyph map and
status-chip labels. No Textual, no color values (colors live in theme.py). The
reducer and the widgets both import these so the iconography stays in one place.
See docs/superpowers/specs/2026-06-26-tui-design-system-design.md §4.3."""

from __future__ import annotations

GLYPH: dict[str, str] = {
    # state dots
    "idle": "•",
    "active": "◐",
    "responding": "▌",
    "tool": "›",
    "done": "✓",
    "failed": "✗",
    "scheduled": "⏱",
    "awaiting": "?",
    # tool subtypes (glyph-only, inferred)
    "edit": "✎",
    "test": "⚑",
    "read": "◇",
    "shell": "$",
    "search": "⌕",
}

STATUS_LABEL: dict[str, str] = {
    "idle": "IDLE",
    "thinking": "THINKING",
    "responding": "RESPONDING",
    "running": "RUNNING",
    "queued": "QUEUED",
    "scheduled": "SCHEDULED",
    "completed": "COMPLETED",
    "done": "COMPLETED",
    "failed": "FAILED",
    "awaiting": "AWAITING",
}
