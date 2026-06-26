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
COLS = 72                  # width in cells; fits the 84-wide landing column


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


# Quadrant glyphs by 4-bit mask (TL=1, TR=2, BL=4, BR=8): each cell is a 2x2
# sub-pixel grid, giving 2x the horizontal/vertical detail of half-blocks.
_QUAD = {
    0: " ", 1: "▘", 2: "▝", 3: "▀", 4: "▖", 5: "▌", 6: "▞", 7: "▛",
    8: "▗", 9: "▚", 10: "▐", 11: "▜", 12: "▄", 13: "▙", 14: "▟", 15: "█",
}


def render_rows():
    im = Image.open(ASSET).convert("RGB")
    w, h = im.size
    rows = max(1, round(h * COLS / w / 2))
    # 2 sub-pixels per cell in each axis -> quadrant resolution.
    im = im.resize((COLS * 2, rows * 2), Image.LANCZOS)
    px = im.load()
    out = []
    for cy in range(rows):
        cells = []
        for cx in range(COLS):
            quads = [
                _quant(px[cx * 2, cy * 2]),       # TL
                _quant(px[cx * 2 + 1, cy * 2]),   # TR
                _quant(px[cx * 2, cy * 2 + 1]),   # BL
                _quant(px[cx * 2 + 1, cy * 2 + 1]),  # BR
            ]
            lit = [q for q in quads if q is not None]
            if not lit:
                cells.append(" ")
                continue
            # A cell renders in a single fg color: pick the majority ink color.
            n_blue = sum(1 for q in lit if q == BLUE)
            fg = BLUE if n_blue >= len(lit) - n_blue else WHITE
            mask = sum(bit for bit, q in zip((1, 2, 4, 8), quads) if q is not None)
            cells.append(f"[{_hex(fg)}]{_QUAD[mask]}[/]")
        out.append("".join(cells))
    return out


HEADER = '''"""The landing-screen brand logo: the DoneDone wordmark rendered as Unicode
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
