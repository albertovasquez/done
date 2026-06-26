"""The harness TUI theme — a single named Textual Theme with semantic tokens, so
the whole UI restyles from one place. Palette is the DoneDone brand identity
(navy base, brand blue accent) — see "Brand Book - DoneDone.pdf" p.13. Custom
design tokens live in `variables` and are referenced in app.tcss as `$muted`,
`$code`, etc.

DoneDone brand colors (Brand Book p.13):
    #286CE9  brand blue  — primary / accent
    #0A1524  dark navy   — background base
    #E3E3E3  light grey  — foreground text
    #8690A3  slate grey  — secondary / muted text
    #E02F07  red-orange  — error / alert
The brand defines no green/amber, so success/warning keep functional colors for
legibility (green = go, amber = caution); inline-code is tinted into the blue
family rather than inventing an off-brand hue.

Reference: docs/learning-log.md (Phase 5 chat UI)."""

from __future__ import annotations

from textual.theme import Theme

# Semantic palette. Change values here → the whole TUI retheme.
HARNESS_THEME = Theme(
    name="harness",
    primary="#286CE9",        # DoneDone blue — accent bar, mode label, wordmark highlight
    secondary="#8690A3",      # brand slate — secondary text (model name, dim labels)
    accent="#286CE9",
    foreground="#E3E3E3",     # brand light grey — body text
    background="#0A1524",     # brand dark navy — app background
    surface="#16243A",        # navy lightened — boxes (user msg, compose)
    panel="#16243A",
    success="#7ee787",        # functional green (not in brand; kept for legibility)
    warning="#e3b341",        # functional amber (not in brand; kept for legibility)
    error="#E02F07",          # brand red-orange
    dark=True,
    variables={
        # custom tokens referenced in app.tcss + markup
        "muted": "#5B6577",          # placeholders, hints, meta lines (slate darkened)
        "code": "#9DB8E8",           # inline code / shell commands (brand-blue tint)
        "wordmark-dim": "#3A4D6B",   # left half of the wordmark (navy-blue dim)
        "wordmark-bright": "#286CE9",# right half of the wordmark (brand blue)
        "accent-bar": "#286CE9",
    },
)

# Raw hex colors for RichLog markup. RichLog content is rendered by RICH, not by
# Textual's CSS engine, so it does NOT understand `$accent`/`$muted` CSS-variable
# references — use these hex values in transcript markup instead.
COLORS = {
    "accent": "#286CE9",
    "muted": "#5B6577",
    "code": "#9DB8E8",
    "foreground": "#E3E3E3",
    "success": "#7ee787",
    "warning": "#e3b341",
    "error": "#E02F07",
    "primary": "#286CE9",
    "secondary": "#8690A3",
}

# Status → hex color for tool-call lines (mirrors render.status_style names).
STATUS_COLOR = {
    "pending": COLORS["warning"],
    "in_progress": COLORS["primary"],
    "completed": COLORS["success"],
    "failed": COLORS["error"],
}
