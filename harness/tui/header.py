"""The landing-screen header: the DoneDone ≡ mark (three blue bars) beside a
short text block (name + tagline + model), in the style of Claude Code's session
banner. Replaces the large wordmark — small, on-brand, and trivially crisp in a
terminal (three solid bars need no quantization).

The icon is plain block glyphs coloured with the brand blue; the text uses
Textual markup ($accent / $muted) so it tracks the theme."""

from __future__ import annotations

BLUE = "#286ce9"

# Three blue bars (the brand ≡ mark), one per row aligned to the three text
# lines. Each bar is a FULL block '█' filling its whole cell, so it visually
# centers on the text row — the midpoint between '▀' (top of cell, floats above
# the text) and '▄' (bottom of cell, sits a touch low). The ≡ separation comes
# from the line gap between the three single-row Static lines, not from empty
# half-cells.
_ICON_ROWS = ["█████", "█████", "█████"]


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
