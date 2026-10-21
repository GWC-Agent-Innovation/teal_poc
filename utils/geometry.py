"""Small 2D geometry helpers (mm). Pure functions, no CAD dependencies."""
from __future__ import annotations

import math

EPS = 1e-6


def circle_inside_rect(cx: float, cy: float, r: float, length: float, width: float) -> bool:
    return cx - r >= -EPS and cx + r <= length + EPS and cy - r >= -EPS and cy + r <= width + EPS


def circles_overlap(c1: tuple[float, float], r1: float, c2: tuple[float, float], r2: float) -> bool:
    return math.dist(c1, c2) < r1 + r2 - EPS


def slot_bbox(cx: float, cy: float, length: float, width: float, along_x: bool) -> tuple[float, float, float, float]:
    """Bounding box (xmin, ymin, xmax, ymax) of a straight slot; length is overall (end to end)."""
    hl, hw = length / 2, width / 2
    return (cx - hl, cy - hw, cx + hl, cy + hw) if along_x else (cx - hw, cy - hl, cx + hw, cy + hl)


def slot_centers(cx: float, cy: float, length: float, width: float, along_x: bool) -> tuple[tuple[float, float], tuple[float, float]]:
    """Centres of the two end arcs of a straight slot."""
    d = (length - width) / 2
    return ((cx - d, cy), (cx + d, cy)) if along_x else ((cx, cy - d), (cx, cy + d))


def point_segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def bbox_inside_rect(bbox: tuple[float, float, float, float], length: float, width: float) -> bool:
    xmin, ymin, xmax, ymax = bbox
    return xmin >= -EPS and ymin >= -EPS and xmax <= length + EPS and ymax <= width + EPS


def circle_rect_gap(c: tuple[float, float], r: float, rect: tuple[float, float, float, float]) -> float:
    """Clearance between a circle and an axis-aligned rectangle (negative = overlap)."""
    xmin, ymin, xmax, ymax = rect
    nx, ny = min(max(c[0], xmin), xmax), min(max(c[1], ymin), ymax)
    return math.dist(c, (nx, ny)) - r


def rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] - EPS and b[0] < a[2] - EPS and a[1] < b[3] - EPS and b[1] < a[3] - EPS


def close(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) <= tol
