"""The landing-screen brand logo: the DoneDone wordmark as Unicode sextant-block
art, hand-authored glyph-by-glyph so each letter is editable in isolation.

Design: the wordmark is "DONE" + its 180° rotation ("DONE" mirrored). We author
the four glyphs D, O, N, E ONCE as sub-pixel bitmaps ('#' = ink, '.' = blank,
15 sub-rows tall = 5 sextant cell-rows). `logo_markup()` lays them out — DONE in
brand blue, then the same four rotated 180° in white for the mirrored half — and
packs each 2x3 sub-pixel block into a sextant glyph (U+1FB00 block) coloured by
the majority ink of its cell. The mirror is generated, so fixing one glyph fixes
both occurrences and keeps the lockup symmetric.

To tweak a letter, edit only its bitmap below (e.g. `_E`) and re-run
scripts/gen_logo.py to preview. No image-library dependency at runtime — the
glyphs are the source of truth; the bundled assets/donedone-logo.png is only the
original reference. Rendered inside a Textual Static, so it never flickers or
gets cleared on repaint and works in any truecolor terminal (Ghostty, Kitty,
WezTerm, iTerm2; sextants are Unicode 13.0).

Brand colors: blue #286CE9, white #E3E3E3, over the navy #0A1524 background
(blank cells)."""

from __future__ import annotations

BLUE = "#286ce9"
WHITE = "#e3e3e3"

# --- the four authored glyphs (edit a single one to fix that letter) ---------

_D = [
    "###########...",
    "#############.",
    "#############.",
    "####.....####.",
    "####......####",
    "####......####",
    "####......####",
    "####......####",
    "####......####",
    "####......####",
    "####......####",
    "####.....####.",
    "#############.",
    "#############.",
    "###########...",
]
_O = [
    "...#######....",
    "..#########...",
    ".####...####..",
    "####.....####.",
    "####.....####.",
    "####.....####.",
    "####.....####.",
    "####.....####.",
    "####.....####.",
    "####.....####.",
    "####.....####.",
    ".####...####..",
    "..#########...",
    "...#######....",
    "..............",
]
_N = [
    "####.......####",
    "#####......####",
    "######.....####",
    "####.##....####",
    "####.###...####",
    "####..###..####",
    "####...###.####",
    "####....######",
    "####.....#####",
    "####......####",
    "####......####",
    "####.......####",
    "####.......####",
    "####.......####",
    "####.......####",
]
# E is the three-bar "equals" glyph (≡). Bars and gaps are each a whole sextant
# cell (3 sub-rows) — bar/gap/bar/gap/bar — so the three bars render identical
# and even, with no straddling notch.
_E = [
    "#############",
    "#############",
    "#############",
    ".............",
    ".............",
    ".............",
    "#############",
    "#############",
    "#############",
    ".............",
    ".............",
    ".............",
    "#############",
    "#############",
    "#############",
]

_GLYPHS = [_D, _O, _N, _E]
_GAP = 2          # blank sub-columns between letters
_ROWS_TALL = 15   # 5 sextant cell-rows


# Sextant glyph by 6-bit mask (cell1=1 cell2=2 / cell3=4 cell4=8 / cell5=16
# cell6=32), built from the U+1FB00 block's Unicode names. The two column
# patterns are the existing half-block glyphs.
def _sextant_map() -> dict[int, str]:
    import unicodedata

    cellbit = {"1": 1, "2": 2, "3": 4, "4": 8, "5": 16, "6": 32}
    m = {0: " ", 63: "█", 21: "▌", 42: "▐"}
    for cp in range(0x1FB00, 0x1FB3C):
        name = unicodedata.name(chr(cp), "")
        if name.startswith("BLOCK SEXTANT-"):
            m[sum(cellbit[d] for d in name.split("SEXTANT-")[1])] = chr(cp)
    return m


def _norm(glyph: list[str]) -> list[str]:
    w = max(len(r) for r in glyph)
    return [r.ljust(w, ".") for r in glyph]


def _rot180(glyph: list[str]) -> list[str]:
    return ["".join(reversed(r)) for r in reversed(_norm(glyph))]


def _sub_grid() -> list[list[str | None]]:
    """Build the full sub-pixel grid: DONE (blue) + rotated DONE (white)."""
    blocks = [(_norm(g), BLUE) for g in _GLYPHS]
    blocks += [(_rot180(g), WHITE) for g in reversed(_GLYPHS)]
    rows: list[list[str | None]] = [[] for _ in range(_ROWS_TALL)]
    for i, (glyph, color) in enumerate(blocks):
        for y in range(_ROWS_TALL):
            rows[y].extend(color if ch == "#" else None for ch in glyph[y])
        if i < len(blocks) - 1:
            for y in range(_ROWS_TALL):
                rows[y].extend([None] * _GAP)
    return rows


def logo_markup() -> str:
    """Return Rich markup for the DoneDone logo as sextant-block art."""
    grid = _sub_grid()
    h = len(grid)
    w = len(grid[0])
    # pad to whole sextant cells (2 wide x 3 tall)
    if w % 2:
        for r in grid:
            r.append(None)
        w += 1
    while h % 3:
        grid.append([None] * w)
        h += 1
    sext = _sextant_map()
    lines = []
    for cy in range(h // 3):
        cells = []
        for cx in range(w // 2):
            subs = [
                grid[cy * 3 + 0][cx * 2 + 0], grid[cy * 3 + 0][cx * 2 + 1],
                grid[cy * 3 + 1][cx * 2 + 0], grid[cy * 3 + 1][cx * 2 + 1],
                grid[cy * 3 + 2][cx * 2 + 0], grid[cy * 3 + 2][cx * 2 + 1],
            ]
            lit = [s for s in subs if s is not None]
            if not lit:
                cells.append(" ")
                continue
            n_blue = sum(1 for s in lit if s == BLUE)
            fg = BLUE if n_blue >= len(lit) - n_blue else WHITE
            mask = sum(bit for bit, s in zip((1, 2, 4, 8, 16, 32), subs) if s is not None)
            cells.append(f"[{fg}]{sext[mask]}[/]")
        lines.append("".join(cells))
    return "\n".join(lines)
