"""Draw fitted entities over the source drawing (visual check)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .fit import FittedLoop, sample
from .linework import Linework

COLORS = {"outline": (0, 0, 230), "region": (0, 150, 0), "cutout": (220, 60, 0)}


def draw(image: Path, lw: Linework, fits: list[FittedLoop], out: Path, crop: bool = True) -> Path:
    img = cv2.cvtColor(cv2.imread(str(image), cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2BGR)
    img = (255 - (255 - img) * 0.35).astype(np.uint8)          # fade the drawing
    k = lw.scale_px_per_mm
    to_px = lambda p: np.c_[p[:, 0] * k + lw.axis_x_px, lw.bottom_px - p[:, 1] * k]  # noqa: E731
    for f in fits:
        for e in f.entities:
            pts = to_px(np.vstack([sample(e, 0.5), e.p1[None]]))
            cv2.polylines(img, [np.round(pts * 4).astype(np.int32)], False, COLORS[f.kind], 1, cv2.LINE_AA, shift=2)
            for p in to_px(np.array([e.p0])):
                cv2.circle(img, tuple(np.round(p).astype(int)), 2, (0, 0, 0), -1)
    if crop:
        o = next(f for f in fits if f.kind == "outline").entities
        allp = to_px(np.vstack([sample(e) for e in o]))
        x0, y0 = np.maximum(allp.min(axis=0).astype(int) - 15, 0)
        x1, y1 = allp.max(axis=0).astype(int) + 15
        img = img[y0:y1, x0:x1]
    img = cv2.resize(img, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(out), img)
    return out
