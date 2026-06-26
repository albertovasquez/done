"""The landing-screen header: the DoneDone ≡ mark (three blue bars) beside a
short text block (name + tagline + model), in the style of Claude Code's session
banner. Replaces the large wordmark — small, on-brand, and trivially crisp in a
terminal (three solid bars need no quantization).

The icon is plain block glyphs coloured with the brand blue; the text uses
Textual markup ($accent / $muted) so it tracks the theme."""

from __future__ import annotations

BLUE = "#286ce9"

# Three blue bars (the brand ≡ mark), one per row aligned to the three text
# lines. Each bar is a LOWER-half block '▄' so it fills the BOTTOM of its cell —
# this seats each bar on the text baseline of its row (an upper-half '▀' floats
# at the top of the cell and reads as sitting above the text). The empty top of
# each cell gives the ≡ separation (stacked full blocks would merge into one
# filled rectangle).
_ICON_ROWS = ["▄▄▄▄▄", "▄▄▄▄▄", "▄▄▄▄▄"]


def icon_markup() -> str:
    """Rich markup for the ≡ icon (three brand-blue bars)."""
    return "\n".join(f"[{BLUE}]{row}[/]" if row.strip() else row for row in _ICON_ROWS)


def header_text_markup(title: str, version: str, tagline: str, model_line: str) -> str:
    """Three-line header text: bold name + dim version / tagline / model · provider.

    `model_line` is the already-formatted 'Build · model provider' markup."""
    return (
        f"[$accent][b]{title}[/b][/] [$muted]v{version}[/]\n"
        f"[$foreground]{tagline}[/]\n"
        f"{model_line}"
    )
