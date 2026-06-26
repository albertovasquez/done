"""The harness TUI theme — a single named Textual Theme with semantic tokens, so
the whole UI restyles from one place. Palette reproduces the opencode look (slate
background, violet accent) in our own code. Custom design tokens live in
`variables` and are referenced in app.tcss as `$muted`, `$code`, etc.

Reference: docs/learning-log.md (Phase 5 chat UI)."""

from __future__ import annotations

from textual.theme import Theme

# Semantic palette. Change values here → the whole TUI retheme.
HARNESS_THEME = Theme(
    name="harness",
    primary="#b794f6",        # violet — accent bar, mode label, wordmark highlight
    secondary="#8b93b0",      # secondary text (model name, dim labels)
    accent="#b794f6",
    foreground="#c8cfe0",     # body text
    background="#262b3d",     # slate-blue app background
    surface="#2d3344",        # boxes (user msg, compose)
    panel="#2d3344",
    success="#7ee787",
    warning="#e3b341",
    error="#f47067",
    dark=True,
    variables={
        # custom tokens referenced in app.tcss + markup
        "muted": "#6b7390",          # placeholders, hints, meta lines
        "code": "#d7e36a",           # inline code / shell commands
        "wordmark-dim": "#4a5270",   # left half of the wordmark
        "wordmark-bright": "#aab3d4",# right half of the wordmark
        "accent-bar": "#b794f6",
    },
)

# Raw hex colors for RichLog markup. RichLog content is rendered by RICH, not by
# Textual's CSS engine, so it does NOT understand `$accent`/`$muted` CSS-variable
# references — use these hex values in transcript markup instead.
COLORS = {
    "accent": "#b794f6",
    "muted": "#6b7390",
    "code": "#d7e36a",
    "foreground": "#c8cfe0",
    "success": "#7ee787",
    "warning": "#e3b341",
    "error": "#f47067",
    "primary": "#b794f6",
    "secondary": "#8b93b0",
}

# Status → hex color for tool-call lines (mirrors render.status_style names).
STATUS_COLOR = {
    "pending": COLORS["warning"],
    "in_progress": COLORS["primary"],
    "completed": COLORS["success"],
    "failed": COLORS["error"],
}
