"""Generate a simple synthetic plate drawing: python -m tests.make_sample_drawing [out.png]"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make(out: Path) -> None:
    W, H = 1200, 800
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        f = ImageFont.load_default()
    x0, y0, x1, y1 = 250, 200, 850, 560  # plate 100 x 60 (scale is deliberately NOT to size info)
    d.rectangle((x0, y0, x1, y1), outline="black", width=3)
    holes = [(x0 + 120, y0 + 90), (x1 - 120, y0 + 90), (x0 + 120, y1 - 90), (x1 - 120, y1 - 90)]
    for cx, cy in holes:
        d.ellipse((cx - 30, cy - 30, cx + 30, cy + 30), outline="black", width=3)
        d.line((cx - 40, cy, cx + 40, cy), fill="black")
        d.line((cx, cy - 40, cx, cy + 40), fill="black")
    # overall dimensions
    d.line((x0, y1 + 60, x1, y1 + 60), fill="black", width=2)
    d.line((x0, y1, x0, y1 + 70), fill="black"); d.line((x1, y1, x1, y1 + 70), fill="black")
    d.text(((x0 + x1) // 2 - 15, y1 + 30), "100", fill="black", font=f)
    d.line((x0 - 60, y0, x0 - 60, y1), fill="black", width=2)
    d.line((x0 - 70, y0, x0, y0), fill="black"); d.line((x0 - 70, y1, x0, y1), fill="black")
    d.text((x0 - 120, (y0 + y1) // 2), "60", fill="black", font=f)
    # hole offsets
    cx, cy = holes[2]
    d.line((x0, y1 + 25, cx, y1 + 25), fill="black"); d.text((x0 + 40, y1 - 40), "20", fill="black", font=f)
    d.line((x1 + 60, cy, x1 + 60, y1), fill="black"); d.text((x1 + 70, cy + 20), "15", fill="black", font=f)
    d.line((x1, cy, x1 + 70, cy), fill="black"); d.line((x1, y1, x1 + 70, y1), fill="black")
    d.text((x0 + 200, y0 - 60), "4X Ø10 THRU", fill="black", font=f)
    d.line((holes[0][0] + 20, holes[0][1] - 20, x0 + 260, y0 - 30), fill="black")
    d.text((60, 30), "TOP", fill="black", font=f)
    d.rectangle((850, 690, 1180, 780), outline="black")
    d.text((860, 700), "PART: RP-001  MATERIAL: -", fill="black", font=f)
    d.text((860, 740), "UNITS: mm   (thickness not shown)", fill="black", font=f)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)


if __name__ == "__main__":
    make(Path(sys.argv[1] if len(sys.argv) > 1 else "input/sample_1.png"))
