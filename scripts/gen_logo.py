#!/usr/bin/env python3
"""Regenerate harness/tui/logo.py from the brand logo asset.

The landing logo is shipped as a static half-block-art string in logo.py so the
runtime needs no image library. This script rebuilds that string from
harness/tui/assets/donedone-logo.png. Run it after replacing the asset:

    python3 scripts/gen_logo.py

Requires Pillow (dev-only): pip install pillow

The asset is the DoneDone wordmark cropped from the brand book (p.22), on the
brand navy background. Pixels are quantized to the two brand logo colors so the
embedded string stays small and the edges stay crisp."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSET = ROOT / "harness" / "tui" / "assets" / "donedone-logo.png"
OUT = ROOT / "harness" / "tui" / "logo.py"

NAVY = (10, 21, 36)        # #0A1524 — background, rendered as blank cells
BLUE = (0x28, 0x6C, 0xE9)  # brand blue
WHITE = (0xE3, 0xE3, 0xE3) # brand white
COLS = 96                  # width in cells; the landing column is sized to fit


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _quant(p):
    """Snap a pixel to the nearest of {navy→blank, blue, white}."""
    dn, db, dw = _dist(p, NAVY), _dist(p, BLUE), _dist(p, WHITE)
    m = min(dn, db, dw)
    if m == dn:
        return None
    return BLUE if m == db else WHITE


def _hex(p):
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


def _build_sextant_map():
    """Map a 6-bit sextant mask -> glyph, authoritatively from Unicode names.

    Sub-cell layout and bit values:
        cell1=1  cell2=2
        cell3=4  cell4=8
        cell5=16 cell6=32
    The U+1FB00 "Symbols for Legacy Computing" block names each glyph
    BLOCK SEXTANT-<digits> (e.g. SEXTANT-235 = cells 2,3,5 filled). The two
    column patterns (left=1,3,5 and right=2,4,6) are not in that block — they
    are the existing LEFT/RIGHT HALF BLOCK glyphs."""
    import unicodedata

    cellbit = {"1": 1, "2": 2, "3": 4, "4": 8, "5": 16, "6": 32}
    m = {0: " ", 63: "█", 21: "▌", 42: "▐"}
    for cp in range(0x1FB00, 0x1FB3C):
        name = unicodedata.name(chr(cp), "")
        if not name.startswith("BLOCK SEXTANT-"):
            continue
        bits = sum(cellbit[d] for d in name.split("SEXTANT-")[1])
        m[bits] = chr(cp)
    assert len(m) == 64, f"sextant map incomplete: {len(m)}"
    return m


_SEXTANT = _build_sextant_map()


def render_rows():
    im = Image.open(ASSET).convert("RGB")
    w, h = im.size
    rows = max(1, round(COLS / (w / h) / 2))
    # 2 sub-pixels wide x 3 tall per cell -> sextant resolution.
    im = im.resize((COLS * 2, rows * 3), Image.LANCZOS)
    px = im.load()
    out = []
    for cy in range(rows):
        cells = []
        for cx in range(COLS):
            # sub-cells top->bottom, left then right (matches cellbit numbering).
            subs = [
                _quant(px[cx * 2, cy * 3]),         # cell1
                _quant(px[cx * 2 + 1, cy * 3]),     # cell2
                _quant(px[cx * 2, cy * 3 + 1]),     # cell3
                _quant(px[cx * 2 + 1, cy * 3 + 1]), # cell4
                _quant(px[cx * 2, cy * 3 + 2]),     # cell5
                _quant(px[cx * 2 + 1, cy * 3 + 2]), # cell6
            ]
            lit = [s for s in subs if s is not None]
            if not lit:
                cells.append(" ")
                continue
            # A cell renders in a single fg color: pick the majority ink color.
            n_blue = sum(1 for s in lit if s == BLUE)
            fg = BLUE if n_blue >= len(lit) - n_blue else WHITE
            mask = sum(bit for bit, s in zip((1, 2, 4, 8, 16, 32), subs) if s is not None)
            cells.append(f"[{_hex(fg)}]{_SEXTANT[mask]}[/]")
        out.append("".join(cells))
    return out


HEADER = '''"""The landing-screen brand logo: the DoneDone wordmark rendered as Unicode
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
'''

FOOTER = ''']


def logo_markup() -> str:
    """Return Rich markup for the DoneDone logo as half-block art."""
    return "\\n".join(_ROWS)
'''


def main():
    rows = render_rows()
    body = "".join("    " + repr(r) + ",\n" for r in rows)
    OUT.write_text(HEADER + body + FOOTER)
    print(f"wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
