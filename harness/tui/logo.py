"""The landing-screen brand logo: the DoneDone wordmark rendered as Unicode
sextant-block art, captured once and embedded as a static string so we ship no
image-library dependency (mirrors wordmark.py's figlet approach).

Each cell is a 2x3 sub-pixel grid drawn with sextant glyphs (U+1FB00 block) for
3x vertical detail per cell — the curves and the mirrored white half read far
more clearly than half- or quarter-blocks. The cell takes the majority ink color
of its lit sub-cells. The source asset is assets/donedone-logo.png (cropped from
the DoneDone brand book, p.22); colors are quantized to the two brand logo
colors — blue #286CE9 and white #E3E3E3 — over a transparent (navy) background,
so the logo blends into the TUI's navy backdrop.

Sextants are Unicode 13.0 (2020); they render in modern terminals (Ghostty,
Kitty, WezTerm, iTerm2). To regenerate after changing the asset, run
scripts/gen_logo.py (see that file).

Rendered inside a Textual Static (not a raw image-protocol escape), so it never
flickers or gets cleared on repaint and works in any truecolor terminal."""

from __future__ import annotations

# Sextant-block art for the DoneDone wordmark (96 cells wide, 6 rows). Generated
# — do not edit by hand; regenerate with scripts/gen_logo.py.
_ROWS = [
    ' [#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/]         [#286ce9]🬞[/][#286ce9]🬭[/][#286ce9]🬵[/][#286ce9]🬹[/][#286ce9]🬹[/][#286ce9]🬭[/][#286ce9]🬭[/]       [#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬏[/]    [#286ce9]🬞[/][#286ce9]🬭[/][#286ce9]🬏[/]    [#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/]    [#286ce9]🬦[/][#286ce9]🬹[/][#286ce9]🬱[/]    [#e3e3e3]🬞[/][#286ce9]🬹[/][#286ce9]🬹[/]       [#286ce9]🬭[/][#e3e3e3]🬵[/][#e3e3e3]🬹[/][#e3e3e3]🬹[/][#286ce9]🬹[/][#e3e3e3]🬭[/][#e3e3e3]🬏[/]        [#286ce9]🬞[/][#e3e3e3]🬭[/][#e3e3e3]🬭[/][#286ce9]🬹[/][#286ce9]🬹[/][#286ce9]🬹[/][#e3e3e3]🬱[/] ',
    ' [#286ce9]█[/][#286ce9]█[/][#286ce9]🬕[/][#286ce9]🬂[/][#286ce9]🬎[/][#286ce9]🬬[/][#286ce9]█[/][#286ce9]🬺[/][#286ce9]🬏[/]    [#286ce9]🬞[/][#286ce9]🬻[/][#286ce9]█[/][#286ce9]🬝[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬎[/][#286ce9]█[/][#286ce9]🬺[/][#286ce9]🬓[/]     [#286ce9]█[/][#286ce9]█[/][#286ce9]█[/][#286ce9]🬱[/]   [#286ce9]▐[/][#286ce9]█[/][#286ce9]▌[/]    [#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/]    [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]▌[/]  [#286ce9]🬞[/][#e3e3e3]🬹[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]█[/]     [#e3e3e3]🬵[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]🬎[/][#e3e3e3]🬂[/][#e3e3e3]🬂[/][#e3e3e3]🬊[/][#e3e3e3]🬬[/][#e3e3e3]█[/][#e3e3e3]🬺[/][#e3e3e3]🬏[/]    [#e3e3e3]🬞[/][#e3e3e3]🬻[/][#e3e3e3]█[/][#e3e3e3]🬝[/][#e3e3e3]🬎[/][#286ce9]🬎[/][#e3e3e3]🬨[/][#e3e3e3]█[/][#e3e3e3]█[/] ',
    ' [#286ce9]█[/][#286ce9]█[/][#286ce9]▌[/]   [#286ce9]🬨[/][#286ce9]█[/][#286ce9]🬺[/]    [#286ce9]🬻[/][#286ce9]█[/][#286ce9]🬕[/]     [#286ce9]🬁[/][#286ce9]█[/][#286ce9]█[/][#286ce9]🬏[/]    [#286ce9]█[/][#286ce9]█[/][#286ce9]🬊[/][#286ce9]█[/][#286ce9]🬺[/][#286ce9]🬏[/] [#286ce9]▐[/][#286ce9]█[/][#286ce9]▌[/]    [#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/]    [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]▌[/] [#286ce9]🬵[/][#e3e3e3]█[/][#e3e3e3]█[/][#286ce9]🬬[/][#e3e3e3]█[/][#e3e3e3]█[/]    [#286ce9]🬦[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]🬀[/]     [#e3e3e3]🬨[/][#e3e3e3]█[/][#e3e3e3]🬺[/]    [#e3e3e3]🬻[/][#e3e3e3]█[/][#e3e3e3]🬕[/]   [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]█[/] ',
    ' [#286ce9]█[/][#286ce9]█[/][#286ce9]▌[/]   [#286ce9]🬷[/][#286ce9]█[/][#286ce9]🬝[/]    [#286ce9]🬬[/][#286ce9]█[/][#286ce9]▌[/]     [#286ce9]🬞[/][#286ce9]█[/][#286ce9]█[/][#286ce9]🬀[/]    [#286ce9]█[/][#286ce9]█[/] [#286ce9]🬁[/][#286ce9]🬬[/][#286ce9]█[/][#286ce9]🬱[/][#286ce9]🬷[/][#286ce9]█[/][#286ce9]▌[/]    [#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/]    [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]🬴[/][#e3e3e3]🬻[/][#e3e3e3]█[/][#e3e3e3]🬝[/][#286ce9]🬀[/][#286ce9]▐[/][#e3e3e3]█[/][#e3e3e3]█[/]    [#286ce9]🬉[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]🬏[/]     [#e3e3e3]🬷[/][#e3e3e3]█[/][#e3e3e3]🬝[/]    [#e3e3e3]🬬[/][#e3e3e3]█[/][#e3e3e3]🬲[/]   [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]█[/] ',
    ' [#286ce9]█[/][#286ce9]█[/][#286ce9]🬲[/][#286ce9]🬭[/][#286ce9]🬵[/][#286ce9]🬻[/][#286ce9]█[/][#286ce9]🬎[/][#286ce9]🬀[/]    [#286ce9]🬁[/][#286ce9]🬬[/][#286ce9]█[/][#286ce9]🬹[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬹[/][#286ce9]█[/][#286ce9]🬝[/][#286ce9]🬄[/]     [#286ce9]█[/][#286ce9]█[/]   [#286ce9]🬁[/][#286ce9]🬬[/][#286ce9]█[/][#286ce9]█[/][#286ce9]▌[/]    [#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/][#286ce9]🬭[/]    [#e3e3e3]▐[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]█[/][#286ce9]🬆[/]  [#286ce9]▐[/][#e3e3e3]█[/][#e3e3e3]█[/]     [#e3e3e3]🬊[/][#e3e3e3]█[/][#e3e3e3]█[/][#e3e3e3]🬹[/][#e3e3e3]🬭[/][#e3e3e3]🬭[/][#e3e3e3]🬵[/][#e3e3e3]🬻[/][#e3e3e3]█[/][#e3e3e3]🬝[/][#e3e3e3]🬀[/]    [#e3e3e3]🬁[/][#e3e3e3]🬬[/][#e3e3e3]█[/][#e3e3e3]🬺[/][#e3e3e3]🬹[/][#286ce9]🬹[/][#e3e3e3]🬷[/][#e3e3e3]█[/][#e3e3e3]█[/] ',
    ' [#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/]         [#286ce9]🬁[/][#286ce9]🬂[/][#286ce9]🬊[/][#286ce9]🬎[/][#286ce9]🬎[/][#286ce9]🬂[/][#286ce9]🬂[/]       [#286ce9]🬊[/][#286ce9]🬊[/]     [#286ce9]🬊[/][#286ce9]🬊[/][#286ce9]🬀[/]    [#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/][#286ce9]🬂[/]    [#286ce9]🬉[/][#286ce9]🬎[/][#e3e3e3]🬆[/][#286ce9]🬀[/]   [#286ce9]🬁[/][#286ce9]🬎[/][#286ce9]🬎[/]       [#286ce9]🬂[/][#e3e3e3]🬊[/][#e3e3e3]🬎[/][#e3e3e3]🬎[/][#286ce9]🬎[/][#e3e3e3]🬂[/][#e3e3e3]🬀[/]        [#286ce9]🬁[/][#e3e3e3]🬂[/][#e3e3e3]🬂[/][#286ce9]🬎[/][#286ce9]🬎[/][#286ce9]🬎[/][#e3e3e3]🬆[/] ',
]


def logo_markup() -> str:
    """Return Rich markup for the DoneDone logo as half-block art."""
    return "\n".join(_ROWS)
