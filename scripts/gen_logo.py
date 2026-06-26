#!/usr/bin/env python3
"""Preview the landing logo (harness/tui/logo.py) as a PNG for visual review.

The logo glyphs are hand-authored directly in logo.py (no longer generated from
an image). This script renders the current glyph definitions to a PNG exactly as
the terminal packs them into sextant cells — so you can eyeball changes after
editing a letter's bitmap.

    python3 scripts/gen_logo.py            # writes /tmp/donedone-logo-preview.png
    python3 scripts/gen_logo.py out.png    # custom output path

Requires Pillow (dev-only): pip install pillow"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.tui import logo  # noqa: E402

NAVY = (10, 21, 36)
RGB = {logo.BLUE: (0x28, 0x6C, 0xE9), logo.WHITE: (0xE3, 0xE3, 0xE3)}
SCALE = 12  # pixels per sub-cell in the preview


def main(out: str = "/tmp/donedone-logo-preview.png") -> None:
    # Rebuild the sub-pixel grid (same source the terminal render uses) and draw
    # it faithfully: each sub-cell is a SCALE x SCALE block in its ink color.
    grid = logo._sub_grid()
    h, w = len(grid), len(grid[0])
    img = Image.new("RGB", (w * SCALE, h * SCALE), NAVY)
    px = img.load()
    for y in range(h):
        for x in range(w):
            c = grid[y][x]
            if c is None:
                continue
            rgb = RGB[c]
            for yy in range(SCALE):
                for xx in range(SCALE):
                    px[x * SCALE + xx, y * SCALE + yy] = rgb
    img.save(out)
    cells = logo.logo_markup().split("\n")
    print(f"wrote {out}  (logo: {len(cells)} rows x {len(cells[0].split('[')) - 1} cells)")


if __name__ == "__main__":
    main(*sys.argv[1:2])
