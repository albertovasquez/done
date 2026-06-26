"""The landing-screen wordmark: a clean figlet 'harness' (figlet 'small' font),
captured once and embedded as a static string so we ship no figlet dependency.
Rendered two-tone (left dim, right bright) via theme tokens for the opencode-style
gradient. Backslashes are doubled so Rich markup renders them literally."""

from __future__ import annotations

# figlet 'small' font output for "harness" (verified). Trailing blank rows trimmed.
_ROWS = [
    r" _                            ",
    r"| |_  __ _ _ _ _ _  ___ ______",
    r"| ' \/ _` | '_| ' \/ -_|_-<_-<",
    r"|_||_\__,_|_| |_||_\___/__/__/",
]


def wordmark_markup() -> str:
    """Return Rich-markup text for the wordmark: left portion dim, right bright.
    Split each row at its midpoint for the two-tone gradient. Backslashes are
    escaped so Rich renders them literally rather than as markup."""
    lines = []
    for row in _ROWS:
        mid = len(row) // 2
        left = row[:mid].replace("\\", "\\\\").replace("[", "\\[")
        right = row[mid:].replace("\\", "\\\\").replace("[", "\\[")
        lines.append(f"[$wordmark-dim]{left}[/][$wordmark-bright]{right}[/]")
    return "\n".join(lines)
