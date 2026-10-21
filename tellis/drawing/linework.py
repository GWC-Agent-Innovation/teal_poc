"""Trace a dimensioned 2D drawing into closed loops.

Part outlines are drawn with thick lines; dimension lines, leaders and centre lines are thin. A 3x3 opening
keeps the thick strokes only. Then:
  outline   = the largest thick-line component (the part's own linework)
  regions   = white areas enclosed by that linework (faces: e.g. a raised face plate, cheek panels)
  cut-outs  = separate closed thick-line shapes inside the outline (eyes, slots, mouth) -> through-cuts
Every loop is moved onto the centre of its stroke (the drawn line is the true edge), then converted to mm
with the drawing's y axis pointing up and x = 0 on the symmetry axis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Polygon

log = logging.getLogger("tellis.drawing.linework")


@dataclass
class Loop:
    kind: str                      # outline | region | cutout
    pts_px: np.ndarray             # (n, 2) image pixels, stroke centre
    area_px: float
    pts_mm: np.ndarray = field(default=None)  # filled by to_mm()


@dataclass
class Linework:
    image_size: tuple[int, int]
    stroke_px: float
    axis_x_px: float
    scale_px_per_mm: float
    bottom_px: float
    loops: list[Loop]

    def to_mm(self, p: np.ndarray) -> np.ndarray:
        return np.c_[(p[:, 0] - self.axis_x_px) / self.scale_px_per_mm, (self.bottom_px - p[:, 1]) / self.scale_px_per_mm]


def _contour(mask: np.ndarray) -> np.ndarray:
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
    return c


def _to_stroke_centre(pts: np.ndarray, offset: float) -> np.ndarray:
    """Grow (offset > 0) or shrink a pixel-boundary polygon onto the stroke centre line."""
    poly = Polygon(pts + 0.5).buffer(0)          # pixel centres -> pixel area edges
    moved = poly.buffer(offset, join_style="mitre" if offset < 0 else "round", quad_segs=4)
    if moved.geom_type != "Polygon":
        moved = max(moved.geoms, key=lambda g: g.area)
    return np.asarray(moved.exterior.coords)[:-1] - 0.5


def _symmetry_axis(mask: np.ndarray) -> float:
    """x that best mirrors the linework onto itself (sub-pixel, by searching around the bbox centre)."""
    ys, xs = np.nonzero(mask)
    c0 = (xs.min() + xs.max()) / 2
    best, best_x = -1.0, c0
    dist = cv2.distanceTransform((1 - mask).astype(np.uint8), cv2.DIST_L2, 3)
    for c in np.arange(c0 - 6, c0 + 6.01, 0.25):
        mx = np.round(2 * c - xs).astype(int)
        ok = (mx >= 0) & (mx < mask.shape[1])
        score = np.mean(dist[ys[ok], mx[ok]] < 1.5)
        if score > best:
            best, best_x = score, c
    log.info("Symmetry axis x = %.2f px (%.1f%% of line pixels mirror onto a line)", best_x, best * 100)
    return float(best_x)


def trace(image_path: Path, overall_height_mm: float, symmetric: bool = True, min_cutout_px: int = 40) -> Linework:
    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"cannot read image {image_path}")
    ink = (gray < 140).astype(np.uint8)
    thick = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(thick, 8)
    if n < 2:
        raise ValueError("no thick part lines found")
    main = 1 + int(np.argmax(st[1:, 4]))
    x, y, w, h = st[main, :4]
    lines = (lab == main).astype(np.uint8)
    # the opening can break thin junctions: restore the original ink right next to the kept strokes
    near = cv2.dilate(lines, np.ones((5, 5), np.uint8)) > 0
    lines = (lines | (near & (ink > 0))).astype(np.uint8)

    axis = _symmetry_axis(lines)
    if symmetric:
        # part lines have a mirror partner; leader arrows and text touching them do not
        mirrored = np.zeros_like(lines)
        cols = np.round(2 * axis - np.arange(lines.shape[1])).astype(int)
        ok = (cols >= 0) & (cols < lines.shape[1])
        mirrored[:, ok] = lines[:, cols[ok]]
        lines = (lines & cv2.dilate(mirrored, np.ones((5, 5), np.uint8))).astype(np.uint8)

    dt = cv2.distanceTransform(lines, cv2.DIST_L2, 3)
    stroke = float(2 * np.percentile(dt[lines > 0], 90))     # full stroke width, px
    half = stroke / 2

    loops: list[Loop] = []
    filled = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    ff = filled.copy()
    cv2.floodFill(ff, None, (0, 0), 1)
    solid = (filled | (1 - ff)).astype(np.uint8)              # everything inside the outer outline
    outer = _to_stroke_centre(_contour(solid), -half)
    loops.append(Loop("outline", outer, Polygon(outer).area))

    free = (1 - lines).astype(np.uint8)
    rn, rl, rs, _ = cv2.connectedComponentsWithStats(free, 4)
    background = rl[0, 0]
    for i in range(1, rn):
        if i == background or rs[i, 4] < 150:
            continue
        pts = _to_stroke_centre(_contour(rl == i), +half)
        loops.append(Loop("region", pts, Polygon(pts).area))

    for i in range(1, n):
        if i == main or max(st[i, 2], st[i, 3]) < min_cutout_px:
            continue
        cx, cy, cw, ch = st[i, :4]
        if not (cx >= x and cy >= y and cx + cw <= x + w and cy + ch <= y + h):
            continue                                          # logo, text, title block
        comp = (lab == i).astype(np.uint8)
        hole = comp.copy()
        cv2.floodFill(hole, None, (0, 0), 1)
        inside = (1 - hole).astype(np.uint8)
        if inside.sum() < 20:
            continue                                          # not a closed shape
        pts = _to_stroke_centre(_contour(inside), +half)
        loops.append(Loop("cutout", pts, Polygon(pts).area))

    top_px, bottom_px = outer[:, 1].min(), outer[:, 1].max()
    scale = (bottom_px - top_px) / overall_height_mm
    lw = Linework((gray.shape[1], gray.shape[0]), stroke, axis, scale, bottom_px, loops)
    for lp in loops:
        lp.pts_mm = lw.to_mm(lp.pts_px)
    log.info("Traced %d loops (stroke %.1f px, %.4f px/mm): %s", len(loops), stroke, scale,
             ", ".join(f"{k}={sum(lp.kind == k for lp in loops)}" for k in ("outline", "region", "cutout")))
    return lw
