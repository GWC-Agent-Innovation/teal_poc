"""Fitted loops -> stage3 cad_plan (runs on the repo's verified SolidWorks executor).

Flat part from a single view:
  base plate   outline, extruded `thickness`
  relief       the largest inner region (e.g. a face plate), raised `relief` on top of the plate
  trim         (big rectangle - outline) cut through all: the relief never overhangs the outline
  through-cuts separate closed shapes (eyes, slots, mouth)
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from shapely.geometry import Point

from stage3.planner.operation_models import CADPlan

from .fit import Arc, FittedLoop, Line, _intersections, _settle_arcs, sample


def normalize_height(fits: list[FittedLoop], height: float) -> float:
    """Scale about the axis / bottom so the outline spans exactly [0, height]; dimensioned radii stay exact."""
    outline = next(f for f in fits if f.kind == "outline")
    ys = np.vstack([sample(e, 0.25) for e in outline.entities])[:, 1]
    y0, k = ys.min(), height / (ys.max() - ys.min())
    for f in fits:
        for e in f.entities:
            for attr in ("p0", "p1") + (("c",) if isinstance(e, Arc) else ()):
                p = getattr(e, attr)
                setattr(e, attr, np.array([p[0] * k, (p[1] - y0) * k]))
            if isinstance(e, Arc):
                e.r = e.snapped if e.snapped else e.r * k
        _settle_arcs(f.entities)
    return k


def push_onto(loop: FittedLoop, target: FittedLoop, near: float = 1.5, by: float = 2.0) -> int:
    """Move the loop's entities that run along `target`'s boundary `by` mm outwards (to be trimmed later)."""
    edge = target.polygon.exterior
    moved = 0
    for e in loop.entities:
        pts = sample(e, 2.0)
        if len(pts) < 2 or max(edge.distance(Point(p)) for p in pts) > near:
            continue
        if isinstance(e, Line):
            t = (e.p1 - e.p0) / np.linalg.norm(e.p1 - e.p0)
            n = np.array([t[1], -t[0]])                    # outward for a CCW loop
            e.p0, e.p1 = e.p0 + by * n, e.p1 + by * n
        else:
            e.r = e.r + by if e.ccw else max(e.r - by, 0.5)
            e.snapped = None
        moved += 1
    if moved:
        n = len(loop.entities)
        for k in range(n):
            a, b = loop.entities[k], loop.entities[(k + 1) % n]
            seen = (a.p1 + b.p0) / 2
            cands = _intersections(a, b)
            p = min(cands, key=lambda q: np.linalg.norm(q - seen)) if cands else seen
            if np.linalg.norm(p - seen) > 3 * by:
                p = seen
            a.p1, b.p0 = p.copy(), p.copy()
        _settle_arcs(loop.entities)
    return moved


class _Ops:
    def __init__(self) -> None:
        self.ops: list[dict[str, Any]] = []

    def add(self, operation: str, **kw: Any) -> str:
        op_id = f"OP{len(self.ops) + 1:03d}"
        self.ops.append({"id": op_id, "operation": operation, **kw})
        return op_id

    def loop(self, sketch: str, f: FittedLoop, feature: str) -> None:
        if len(f.entities) == 1 and isinstance(f.entities[0], Arc):     # a round hole / disc
            e = f.entities[0]
            self.add("circle", sketch=sketch, center=tuple(e.c), diameter=2 * e.r, feature_id=feature)
            return
        for e in f.entities:
            if np.linalg.norm(e.p1 - e.p0) < 1e-6:
                continue
            if isinstance(e, Line):
                self.add("line", sketch=sketch, start=tuple(e.p0), end=tuple(e.p1), feature_id=feature)
            else:
                self.add("arc", sketch=sketch, center=tuple(e.c), start=tuple(e.p0), end=tuple(e.p1),
                         direction="ccw" if e.ccw else "cw", feature_id=feature)


def build_plan(fits: list[FittedLoop], name: str, thickness: float, relief: float) -> dict[str, Any]:
    outline = next(f for f in fits if f.kind == "outline")
    regions = [f for f in fits if f.kind == "region"]
    cutouts = [f for f in fits if f.kind == "cutout"]
    b = _Ops()

    s = b.add("create_sketch", plane="XY", offset=0.0, feature_id="plate")
    b.loop(s, outline, "plate")
    b.add("extrude", profile=s, distance=thickness, direction="normal", feature_id="plate")

    face = max(regions, key=lambda f: f.polygon.area) if regions and relief > 0 else None
    if face is not None:
        s = b.add("create_sketch", plane="XY", offset=thickness, feature_id="relief")
        b.loop(s, face, "relief")
        b.add("extrude", profile=s, distance=relief, direction="normal", feature_id="relief")
        x0, y0, x1, y1 = outline.polygon.bounds
        s = b.add("create_sketch", plane="XY", offset=0.0, feature_id="trim")
        b.add("rectangle", sketch=s, origin=(x0 - 20, y0 - 20), length=x1 - x0 + 40, width=y1 - y0 + 40, feature_id="trim")
        b.loop(s, outline, "trim")
        b.add("cut_extrude", profile=s, extent="through_all_both", direction="reverse", feature_id="trim")

    for i, c in enumerate(cutouts, 1):
        s = b.add("create_sketch", plane="XY", offset=0.0, feature_id=f"cutout_{i}")
        b.loop(s, c, f"cutout_{i}")
        b.add("cut_extrude", profile=s, extent="through_all_both", direction="reverse", feature_id=f"cutout_{i}")

    base = outline.polygon
    holes = sum(c.polygon.area for c in cutouts)
    vol = base.area * thickness - holes * thickness
    if face is not None:
        vol += (face.polygon.intersection(base).area - holes) * relief
    x0, y0, x1, y1 = base.bounds
    plan = {"part_name": name, "units": "mm", "source_status": "PASS", "operations": b.ops,
            "expected": {"bbox_mm": [round(x1 - x0, 3), round(y1 - y0, 3), thickness + (relief if face else 0)],
                         "volume_mm3": round(vol, 1)}}
    return CADPlan.model_validate(plan).model_dump(mode="json", exclude_none=True)


def entities_json(fits: list[FittedLoop]) -> list[dict[str, Any]]:
    out = []
    for f in fits:
        ents = []
        for e in f.entities:
            if isinstance(e, Line):
                ents.append({"type": "line", "start": e.p0.round(4).tolist(), "end": e.p1.round(4).tolist(),
                             "length": round(float(np.linalg.norm(e.p1 - e.p0)), 3), "snapped": e.snapped or None})
            else:
                ents.append({"type": "arc", "center": e.c.round(4).tolist(), "radius": round(float(e.r), 3),
                             "start": e.p0.round(4).tolist(), "end": e.p1.round(4).tolist(),
                             "direction": "ccw" if e.ccw else "cw", "snapped_to_dimension": e.snapped})
        out.append({"kind": f.kind, "mirrored": f.mirrored, "max_deviation_mm": round(f.max_dev, 3), "entities": ents})
    return out
