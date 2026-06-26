"""The landing-screen brand logo: the DoneDone wordmark rendered as Unicode
quadrant-block art, captured once and embedded as a static string so we ship no
image-library dependency (mirrors wordmark.py's figlet approach).

Each cell is a 2x2 sub-pixel grid drawn with quadrant glyphs (▘▝▖▗▌▐▞▚▀▄█ …) for
2x the detail of half-blocks; the cell takes the majority ink color of its lit
quadrants. The source asset is assets/donedone-logo.png (cropped from the
DoneDone brand book, p.22); colors are quantized to the two brand logo colors —
blue #286CE9 and white #E3E3E3 — over a transparent (navy) background, so the
logo blends into the TUI's navy backdrop.

To regenerate after changing the asset, run scripts/gen_logo.py (see that file).

Rendered inside a Textual Static (not a raw image-protocol escape), so it never
flickers or gets cleared on repaint and works in any truecolor terminal."""

from __future__ import annotations

# Quadrant-block art for the DoneDone wordmark (72 cells wide, 5 rows). Generated
# — do not edit by hand; regenerate with scripts/gen_logo.py.
_ROWS = [
    ' [#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▖[/]      [#286ce9]▗[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▖[/]    [#286ce9]▗[/][#286ce9]▄[/][#286ce9]▖[/]   [#286ce9]▄[/][#286ce9]▖[/]   [#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/][#286ce9]▄[/]   [#e3e3e3]▗[/][#e3e3e3]▄[/]   [#e3e3e3]▗[/][#e3e3e3]▄[/][#e3e3e3]▖[/]    [#286ce9]▗[/][#e3e3e3]▄[/][#e3e3e3]▄[/][#e3e3e3]▄[/][#e3e3e3]▄[/][#286ce9]▄[/]      [#286ce9]▄[/][#e3e3e3]▄[/][#e3e3e3]▄[/][#e3e3e3]▄[/][#e3e3e3]▄[/] ',
    ' [#286ce9]█[/][#286ce9]▌[/] [#286ce9]▀[/][#286ce9]▜[/][#286ce9]▙[/]    [#286ce9]▟[/][#286ce9]▛[/][#286ce9]▘[/]  [#286ce9]▀[/][#286ce9]█[/][#286ce9]▖[/]   [#286ce9]▐[/][#286ce9]█[/][#286ce9]█[/][#286ce9]▄[/]  [#286ce9]█[/][#286ce9]▌[/]            [#e3e3e3]▐[/][#e3e3e3]█[/]  [#e3e3e3]▟[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]▌[/]   [#286ce9]▟[/][#e3e3e3]█[/][#e3e3e3]▀[/][#286ce9]▀[/][#286ce9]▝[/][#286ce9]▀[/][#e3e3e3]█[/][#e3e3e3]█[/][#286ce9]▖[/]  [#286ce9]▗[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]▀[/][#286ce9]▀[/][#e3e3e3]▜[/][#e3e3e3]█[/] ',
    ' [#286ce9]█[/][#286ce9]▌[/]  [#286ce9]▐[/][#286ce9]█[/][#286ce9]▌[/]  [#286ce9]▐[/][#286ce9]█[/][#286ce9]▌[/]    [#286ce9]▐[/][#286ce9]█[/]   [#286ce9]▐[/][#286ce9]█[/] [#286ce9]▜[/][#286ce9]▙[/][#286ce9]▖[/][#286ce9]█[/][#286ce9]▌[/]   [#286ce9]█[/][#286ce9]█[/][#286ce9]█[/][#286ce9]█[/][#286ce9]█[/][#286ce9]█[/]   [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]▗[/][#e3e3e3]█[/][#e3e3e3]▛[/][#286ce9]▘[/][#e3e3e3]█[/][#e3e3e3]▌[/]   [#e3e3e3]█[/][#286ce9]█[/]    [#286ce9]▐[/][#e3e3e3]█[/][#e3e3e3]▌[/]  [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]▌[/]  [#e3e3e3]▐[/][#e3e3e3]█[/] ',
    ' [#286ce9]█[/][#286ce9]▌[/] [#286ce9]▄[/][#286ce9]▟[/][#286ce9]▛[/]    [#286ce9]▜[/][#286ce9]▙[/][#286ce9]▖[/]  [#286ce9]▄[/][#286ce9]█[/][#286ce9]▘[/]   [#286ce9]▐[/][#286ce9]█[/]  [#286ce9]▀[/][#286ce9]█[/][#286ce9]█[/][#286ce9]▌[/]            [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]▛[/]  [#e3e3e3]█[/][#e3e3e3]▌[/]   [#286ce9]▜[/][#e3e3e3]█[/][#e3e3e3]▄[/][#286ce9]▖[/][#286ce9]▗[/][#286ce9]▄[/][#e3e3e3]▟[/][#e3e3e3]█[/][#286ce9]▘[/]  [#286ce9]▝[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]▄[/][#286ce9]▄[/][#e3e3e3]▟[/][#e3e3e3]█[/] ',
    ' [#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▘[/]      [#286ce9]▝[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▘[/]    [#286ce9]▝[/][#286ce9]▀[/]   [#286ce9]▝[/][#286ce9]▀[/][#286ce9]▘[/]   [#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/][#286ce9]▀[/]   [#e3e3e3]▝[/][#e3e3e3]▀[/][#e3e3e3]▘[/]   [#e3e3e3]▀[/][#e3e3e3]▘[/]    [#286ce9]▝[/][#e3e3e3]▀[/][#e3e3e3]▀[/][#e3e3e3]▀[/][#e3e3e3]▀[/][#286ce9]▀[/]      [#286ce9]▀[/][#e3e3e3]▀[/][#e3e3e3]▀[/][#e3e3e3]▀[/][#e3e3e3]▀[/] ',
]


def logo_markup() -> str:
    """Return Rich markup for the DoneDone logo as half-block art."""
    return "\n".join(_ROWS)
