"""The landing-screen header: the DoneDone ≡ mark (three blue bars) beside a
short text block (name + tagline + model), in the style of Claude Code's session
banner. Replaces the large wordmark — small, on-brand, and trivially crisp in a
terminal (three solid bars need no quantization).

The icon is plain block glyphs coloured with the brand blue; the text uses
Textual markup ($accent / $muted) so it tracks the theme."""

from __future__ import annotations

from harness.tui.theme import COLORS

BLUE = COLORS["accent"]

# The brand ≡ mark: three heavy box-drawing rules '━', one per text row. Unlike a
# half-block ('▀'/'▄'), '━' is a MID-CELL horizontal stroke, so it renders on the
# text's vertical center and lines up cleanly with each text row — no top/bottom-
# half offset to fight, and the row gaps keep the three rules distinct (the ≡).
_ICON_ROWS = ["▄▄▄▄▄", "▄▄▄▄▄", "▄▄▄▄▄"]


def icon_markup() -> str:
    """Rich markup for the ≡ icon (three brand-blue bars)."""
    return "\n".join(f"[{BLUE}]{row}[/]" if row.strip() else row for row in _ICON_ROWS)


def header_text_markup(title: str, version: str, tagline: str) -> str:
    """Three-line header text: bold name + dim version / tagline + muted rule.

    The mode·model line is intentionally NOT here — it lives on the compose-meta
    line under the input (and the status bar), so repeating it in the header was
    redundant."""
    underline = "─" * max(8, len(tagline))
    return (
        f"[$accent][b]{title}[/b][/] [$muted]v{version}[/]\n"
        f"[$foreground]{tagline}[/]\n"
        f"[$muted]{underline}[/]"
    )
