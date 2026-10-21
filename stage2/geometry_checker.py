"""Mathematical feasibility of the solved geometry. Each check is a small function."""
from __future__ import annotations

import itertools
import logging
import math
from typing import Iterator, Optional

from utils import geometry as g

from .models import Boss, Chamfer, Context, Cutout, Drawing, Fillet, Gusset, Hole, Lug, Pocket, Position, Slot

log = logging.getLogger("stage2.geometry")

MIN_WALL_MM = 0.5  # thinner webs between cut features are flagged for review (tangency breaks CAD profiles)


def _v(m) -> Optional[float]:
    return m.value if m is not None else None


def _located(f) -> Iterator[tuple[int, Position]]:
    for i, p in enumerate(f.instances):
        if p.x is not None and p.y is not None:
            yield i, p


def _base(drawing: Drawing) -> tuple[Optional[float], Optional[float], Optional[float]]:
    b = drawing.base
    return (_v(b.length), _v(b.width), _v(b.thickness)) if b else (None, None, None)


def _slot_geom(s: Slot, p: Position):
    along_x = s.orientation == "along_x"
    return along_x, g.slot_centers(p.x, p.y, s.length.value, s.width.value, along_x)  # type: ignore[arg-type]


def _slot_ok(s: Slot) -> bool:
    return s.orientation is not None and _v(s.length) is not None and _v(s.width) is not None


def _concentric_with_boss(h: Hole, p: Position, drawing: Drawing) -> Optional[Boss]:
    for b in drawing.features:
        if isinstance(b, Boss):
            for _, bp in _located(b):
                if g.close(bp.x, p.x) and g.close(bp.y, p.y):  # type: ignore[arg-type]
                    return b
    return None


def check_features_inside_base(drawing: Drawing, ctx: Context) -> None:
    L, W, _ = _base(drawing)
    if L is None or W is None:
        return
    for f in drawing.features:
        for i, p in _located(f):
            tag = f"{f.id}#{i + 1}"
            if isinstance(f, (Hole, Boss)) and _v(f.diameter) is not None:
                r = f.diameter.value / 2  # type: ignore[union-attr]
                if not g.circle_inside_rect(p.x, p.y, r, L, W):  # type: ignore[arg-type]
                    ctx.add("FAIL", "geometry", "FEATURE_OUTSIDE_BASE",
                            f"{tag}: Ø{2 * r:g} at ({p.x:g}, {p.y:g}) extends outside the {L:g} x {W:g} base.",
                            f.id, x=p.x, y=p.y, radius=r, x_min=p.x - r, x_max=p.x + r,  # type: ignore[operator]
                            y_min=p.y - r, y_max=p.y + r, base_length=L, base_width=W)  # type: ignore[operator]
            elif isinstance(f, Slot) and _slot_ok(f):
                bb = g.slot_bbox(p.x, p.y, f.length.value, f.width.value, f.orientation == "along_x")  # type: ignore
                if not g.bbox_inside_rect(bb, L, W):
                    ctx.add("FAIL", "geometry", "FEATURE_OUTSIDE_BASE",
                            f"{tag}: slot extends outside the base (bbox {tuple(round(v, 3) for v in bb)}).",
                            f.id, bbox=bb, base_length=L, base_width=W)
            elif isinstance(f, (Pocket, Cutout)) and _v(f.length) is not None and _v(f.width) is not None:
                bb = (p.x - f.length.value / 2, p.y - f.width.value / 2,  # type: ignore
                      p.x + f.length.value / 2, p.y + f.width.value / 2)  # type: ignore
                if not g.bbox_inside_rect(bb, L, W):
                    ctx.add("FAIL", "geometry", "FEATURE_OUTSIDE_BASE",
                            f"{tag}: {f.type} extends outside the base.", f.id, bbox=bb)


def check_depths(drawing: Drawing, ctx: Context) -> None:
    _, _, T = _base(drawing)
    for f in drawing.features:
        depth = _v(getattr(f, "depth", None))
        if depth is None:
            continue
        if depth <= 0:
            ctx.add("FAIL", "geometry", "NEGATIVE_DEPTH", f"{f.id}: depth {depth:g} must be > 0.", f.id)
        elif T is not None and isinstance(f, (Hole, Slot)) and depth > T and not any(
                _concentric_with_boss(f, p, drawing) for _, p in _located(f) if isinstance(f, Hole)):
            ctx.add("FAIL", "geometry", "DEPTH_EXCEEDS_BASE",
                    f"{f.id}: blind depth {depth:g} exceeds base thickness {T:g}.", f.id)


def check_bores(drawing: Drawing, ctx: Context) -> None:
    for h in drawing.features:
        if not isinstance(h, Hole) or _v(h.diameter) is None:
            continue
        for i, p in _located(h):
            boss = _concentric_with_boss(h, p, drawing)
            if boss and _v(boss.diameter) is not None and h.diameter.value >= boss.diameter.value:  # type: ignore
                ctx.add("FAIL", "geometry", "BORE_LARGER_THAN_BOSS",
                        f"{h.id}#{i + 1}: Ø{h.diameter.value:g} bore is not smaller than boss "  # type: ignore
                        f"{boss.id} Ø{boss.diameter.value:g}.", h.id)  # type: ignore


def _circles(drawing: Drawing) -> list[tuple[str, tuple[float, float], float]]:
    out = []
    for f in drawing.features:
        if isinstance(f, Hole) and _v(f.diameter) is not None:
            for i, p in _located(f):
                out.append((f"{f.id}#{i + 1}", (p.x, p.y), f.diameter.value / 2))  # type: ignore
    return out


def check_overlaps(drawing: Drawing, ctx: Context) -> None:
    circles = _circles(drawing)
    for (a, ca, ra), (b, cb, rb) in itertools.combinations(circles, 2):
        wall = math.dist(ca, cb) - ra - rb
        if g.close(ca[0], cb[0]) and g.close(ca[1], cb[1]) and g.close(ra, rb):
            ctx.add("REVIEW_REQUIRED", "geometry", "DUPLICATE_FEATURE",
                    f"{a} and {b} are identical holes at the same position.", a.split("#")[0])
        elif wall < -g.EPS:
            ctx.add("REVIEW_REQUIRED", "geometry", "OVERLAPPING_HOLES",
                    f"{a} and {b} overlap (centre distance {math.dist(ca, cb):g} < {ra + rb:g}).", a.split("#")[0])
        elif wall < MIN_WALL_MM:
            ctx.add("REVIEW_REQUIRED", "geometry", "ZERO_WALL_THICKNESS",
                    f"{a} and {b} leave a {wall:.3g} mm wall (tangent or nearly touching).", a.split("#")[0], wall=wall)
    for s in drawing.features:
        if not isinstance(s, Slot) or not _slot_ok(s):
            continue
        for si, p in _located(s):
            _, (e1, e2) = _slot_geom(s, p)
            for name, c, r in circles:
                wall = g.point_segment_distance(c, e1, e2) - r - s.width.value / 2  # type: ignore
                if wall < -g.EPS:
                    ctx.add("REVIEW_REQUIRED", "geometry", "HOLE_SLOT_OVERLAP",
                            f"{name} overlaps slot {s.id}#{si + 1}.", s.id)
                elif wall < MIN_WALL_MM:
                    ctx.add("REVIEW_REQUIRED", "geometry", "ZERO_WALL_THICKNESS",
                            f"{name} and slot {s.id}#{si + 1} leave a {wall:.3g} mm wall (tangent or nearly touching).",
                            s.id, wall=wall)


def _corner_edges(drawing: Drawing) -> list[tuple[str, str, float]]:
    out = []
    for f in drawing.features:
        if isinstance(f, Fillet) and f.edges == "base_vertical_corners" and _v(f.radius) is not None:
            out.append((f.id, "fillet", f.radius.value))  # type: ignore
        if isinstance(f, Chamfer) and f.edges == "base_vertical_corners" and _v(f.distance) is not None:
            out.append((f.id, "chamfer", f.distance.value))  # type: ignore
    return out


def check_edge_treatments(drawing: Drawing, ctx: Context) -> None:
    L, W, _ = _base(drawing)
    edges = _corner_edges(drawing)
    if len(edges) > 1:
        ctx.add("REVIEW_REQUIRED", "geometry", "CONFLICTING_EDGE_TREATMENT",
                "more than one fillet/chamfer is applied to the base corner edges: "
                + ", ".join(f"{fid} ({kind} {size:g})" for fid, kind, size in edges), edges[0][0])
    if L is None or W is None:
        return
    corners = [(0.0, 0.0, 1, 1), (L, 0.0, -1, 1), (0.0, W, 1, -1), (L, W, -1, -1)]
    for fid, kind, size in edges:
        for name, c, r in _circles(drawing):
            for cx, cy, sx, sy in corners:
                if _hole_hits_corner_cut((c[0] - cx) * sx, (c[1] - cy) * sy, r, kind, size):
                    ctx.add("FAIL", "geometry", "HOLE_INTERSECTS_EDGE_TREATMENT",
                            f"{name} breaks into the {kind} {fid} at corner ({cx:g}, {cy:g}).", fid)


def _removed_by_corner(lx: float, ly: float, kind: str, size: float) -> bool:
    """Is local point (from the corner, inward positive) in material removed by the corner cut?"""
    if lx < 0 or ly < 0 or lx >= size or ly >= size:
        return False
    if kind == "chamfer":
        return lx + ly < size - g.EPS
    return math.dist((lx, ly), (size, size)) > size + g.EPS  # outside the fillet arc


def _hole_hits_corner_cut(lx: float, ly: float, r: float, kind: str, size: float, samples: int = 180) -> bool:
    pts = [(lx, ly)] + [(lx + r * math.cos(2 * math.pi * k / samples), ly + r * math.sin(2 * math.pi * k / samples))
                        for k in range(samples)]
    return any(_removed_by_corner(px, py, kind, size) for px, py in pts)


def check_duplicates(drawing: Drawing, ctx: Context) -> None:
    seen: dict[tuple, str] = {}
    for f in drawing.features:
        if isinstance(f, (Fillet, Chamfer)) or f.type == "unsupported":
            continue
        sizes = tuple(round(m.value, 3) for m in f.measurements() if m.value is not None)
        pos = tuple((round(p.x, 3), round(p.y, 3)) for _, p in _located(f))  # type: ignore
        if not pos:
            continue
        key = (f.type, sizes, pos)
        if key in seen:
            ctx.add("REVIEW_REQUIRED", "geometry", "DUPLICATE_FEATURE",
                    f"{f.id} duplicates {seen[key]} (same type, size and position).", f.id)
        seen[key] = f.id


def lug_footprint(f: Lug, p: Position) -> Optional[tuple[float, float, float, float]]:
    t, w = _v(f.thickness), _v(f.width)
    if t is None or w is None or f.normal_axis is None:
        return None
    hx, hy = (t / 2, w / 2) if f.normal_axis == "x" else (w / 2, t / 2)
    return (p.x - hx, p.y - hy, p.x + hx, p.y + hy)  # type: ignore[operator]


def gusset_footprint(g: Gusset, pl: dict) -> Optional[tuple[float, float, float, float]]:
    run, th = _v(g.run), _v(g.thickness)
    if run is None or th is None:
        return None
    a = pl["axis"]
    other = "y" if a == "x" else "x"
    lo, hi = sorted((pl[f"{a}0"], pl[f"{a}0"] + pl["dir"] * (run + pl.get("run_offset", 0.0))))
    c = pl[f"{other}0"]
    return (lo, c - th / 2, hi, c + th / 2) if a == "x" else (c - th / 2, lo, c + th / 2, hi)


def check_lugs(drawing: Drawing, ctx: Context) -> None:
    L, W, _ = _base(drawing)
    from .dimension_validator import lug_top_radius
    prints: list[tuple[str, tuple]] = []
    for f in drawing.features:
        if not isinstance(f, Lug):
            continue
        for i, p in _located(f):
            fp = lug_footprint(f, p)
            if fp is None:
                continue
            tag = f"{f.id}#{i + 1}"
            prints.append((tag, fp))
            if L is not None and W is not None and not g.bbox_inside_rect(fp, L, W):
                ctx.add("FAIL", "geometry", "FEATURE_OUTSIDE_BASE", f"{tag}: lug footprint {fp} leaves the base.", f.id)
        d, hh, h, w = _v(f.hole_diameter), _v(f.hole_height), _v(f.height), _v(f.width)
        if None in (d, hh, h, w):
            continue
        r, R = d / 2, lug_top_radius(f)  # type: ignore[operator]
        walls = {"bottom": hh - r, "side": w / 2 - r}  # type: ignore[operator]
        walls["top"] = (R - (abs((h - R) - hh)) - r) if R is not None and hh >= h - R else h - hh - r  # type: ignore
        for where, wall in walls.items():
            if wall < -g.EPS:
                ctx.add("FAIL", "geometry", "LUG_HOLE_OUTSIDE_LUG", f"{f.id}: Ø{d:g} hole breaks out of the lug ({where}).", f.id)
            elif wall < MIN_WALL_MM:
                ctx.add("REVIEW_REQUIRED", "geometry", "ZERO_WALL_THICKNESS",
                        f"{f.id}: Ø{d:g} hole leaves a {wall:.3g} mm {where} wall in the lug.", f.id, wall=wall)
    for (a, fa), (b, fb) in itertools.combinations(prints, 2):
        if g.rects_overlap(fa, fb):
            ctx.add("REVIEW_REQUIRED", "geometry", "OVERLAPPING_LUGS", f"{a} and {b} overlap.", a.split("#")[0])
    for name, c, r in _circles(drawing):
        for tag, fp in prints:
            if g.circle_rect_gap(c, r, fp) < 0:
                ctx.add("REVIEW_REQUIRED", "geometry", "HOLE_UNDER_LUG", f"{name} lies under lug {tag}.", tag.split("#")[0])


def check_gussets(drawing: Drawing, ctx: Context) -> None:
    L, W, _ = _base(drawing)
    lug_prints = [(f"{f.id}#{i + 1}", lug_footprint(f, p)) for f in drawing.features if isinstance(f, Lug)
                  for i, p in _located(f)]
    for gs in drawing.features:
        if not isinstance(gs, Gusset):
            continue
        for i, pl in enumerate(gs.placements):
            fp = gusset_footprint(gs, pl)
            if fp is None:
                continue
            tag = f"{gs.id}#{i + 1}"
            if L is not None and W is not None and not g.bbox_inside_rect(fp, L, W):
                ctx.add("FAIL", "geometry", "FEATURE_OUTSIDE_BASE",
                        f"{tag}: gusset footprint {tuple(round(v, 3) for v in fp)} leaves the base.", gs.id)
            for name, c, r in _circles(drawing):
                if g.circle_rect_gap(c, r, fp) < 0:
                    ctx.add("REVIEW_REQUIRED", "geometry", "HOLE_UNDER_GUSSET", f"{name} lies under gusset {tag}.", gs.id)
            for lt, lf in lug_prints:
                if lf and not lt.startswith(gs.attached_to or "\0") and g.rects_overlap(fp, lf):
                    ctx.add("REVIEW_REQUIRED", "geometry", "GUSSET_HITS_LUG", f"{tag} runs into lug {lt}.", gs.id)
        # the lug's cross hole is cut through everything on its axis: the gusset must stay below it
        lug = drawing.feature(gs.attached_to or "")
        if isinstance(lug, Lug) and None not in (_v(lug.hole_diameter), _v(lug.hole_height), _v(gs.rise)):
            gap = (lug.hole_height.value - lug.hole_diameter.value / 2) - gs.rise.value  # type: ignore[union-attr]
            if gap < MIN_WALL_MM:
                ctx.add("REVIEW_REQUIRED", "geometry", "GUSSET_BLOCKS_LUG_HOLE",
                        f"{gs.id}: top of the gusset (rise {gs.rise.value:g}) is {gap:.3g} mm from the bottom of "  # type: ignore
                        f"{lug.id}'s Ø{lug.hole_diameter.value:g} hole; the hole would cut into it.", gs.id, gap=gap)  # type: ignore


CHECKS = [check_features_inside_base, check_depths, check_bores, check_overlaps, check_edge_treatments,
          check_duplicates, check_lugs, check_gussets]


def check(drawing: Drawing, ctx: Context) -> None:
    for fn in CHECKS:
        fn(drawing, ctx)
    log.info("Geometry feasibility: %s", ctx.stage_status("geometry").value)
