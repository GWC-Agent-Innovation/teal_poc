"""Turn canonical relationships into explicit X/Y/Z coordinates for every feature instance.

Only relationships backed by Stage 1 evidence are used. An axis with no constraint, or with
contradicting constraints, is left as None and reported as REVIEW_REQUIRED.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from utils.geometry import close

from .models import POSITIONED_TYPES, Boss, Context, Drawing, FeatureRelationship, Gusset, Hole, Lug, Position, Slot

log = logging.getLogger("stage2.coordinates")

Candidates = dict[str, list[tuple[float, str]]]  # axis -> [(value, source)]


def _n_instances(f) -> int:
    return f.quantity if f.quantity and f.quantity > 0 else 1


def _half_extent(f, axis: str) -> Optional[float]:
    """Distance from a feature's centre to its edge along an axis."""
    if isinstance(f, (Hole, Boss)):
        return f.diameter.value / 2 if f.diameter and f.diameter.value is not None else None
    if isinstance(f, Lug):
        if f.normal_axis is None:
            return None
        m = f.thickness if axis == f.normal_axis else f.width
        return m.value / 2 if m is not None and m.value is not None else None
    if isinstance(f, Slot):
        if f.orientation is None or f.length is None or f.width is None:
            return None
        along = (f.orientation == "along_x") == (axis == "x")
        m = f.length if along else f.width
        return m.value / 2 if m.value is not None else None
    m = getattr(f, "length" if axis == "x" else "width", None)
    return m.value / 2 if m is not None and m.value is not None else None


def _target_offset(f, rel: FeatureRelationship, ctx: Context) -> Optional[float]:
    """Offset from the dimensioned point to the feature centre (towards the part interior)."""
    target = rel.target
    if target is None:
        if isinstance(f, (Hole, Boss)):
            return 0.0  # holes/bosses are dimensioned to their centre (drafting convention)
        ctx.add("REVIEW_REQUIRED", "relationships", "AMBIGUOUS_TARGET",
                f"{f.id}: {rel.kind} does not say whether it measures to the centre or an edge.", f.id,
                relationship=rel.raw)
        return None
    if target == "center":
        return 0.0
    if target == "edge":
        h = _half_extent(f, rel.axis or "x")
        if h is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "AMBIGUOUS_TARGET",
                    f"{f.id}: edge-referenced dimension but the feature size along {rel.axis} is unknown.", f.id)
        return h
    if target == "arc_center" and isinstance(f, Slot) and f.orientation and f.length and f.width \
            and f.length.value is not None and f.width.value is not None:
        along = (f.orientation == "along_x") == (rel.axis == "x")
        return (f.length.value - f.width.value) / 2 if along else 0.0
    ctx.add("REVIEW_REQUIRED", "relationships", "AMBIGUOUS_TARGET",
            f"{f.id}: dimension target '{rel.raw.get('target')}' is not understood.", f.id, relationship=rel.raw)
    return None


class _Solver:
    def __init__(self, drawing: Drawing, ctx: Context):
        self.d, self.ctx = drawing, ctx
        b = drawing.base
        self.L = b.length.value if b and b.length else None
        self.W = b.width.value if b and b.width else None
        self.T = b.thickness.value if b and b.thickness else None
        self.feats = {f.id: f for f in drawing.features if f.type in POSITIONED_TYPES}
        self.cand: dict[str, list[Candidates]] = {
            fid: [{"x": [], "y": [], "z": []} for _ in range(_n_instances(f))] for fid, f in self.feats.items()}

    # -- helpers ---------------------------------------------------------------------------------
    def known(self, fid: str, inst: int, axis: str) -> Optional[float]:
        c = self.cand.get(fid)
        if c is None or inst >= len(c) or not c[inst][axis]:
            return None
        return c[inst][axis][0][0]

    def put(self, fid: str, inst: int, axis: str, value: float, source: str) -> None:
        self.cand[fid][inst][axis].append((value, source))
        self.ctx.trace.append(f"{fid}#{inst + 1}.{axis} = {value:g} mm  <- {source}")

    def instances_for(self, rel: FeatureRelationship, has_pattern: bool) -> Optional[list[int]]:
        n = len(self.cand[rel.feature_id])
        if rel.instance is not None:
            if not 1 <= rel.instance <= n:
                self.ctx.add("REVIEW_REQUIRED", "relationships", "INSTANCE_OUT_OF_RANGE",
                             f"{rel.feature_id}: instance {rel.instance} but quantity is {n}.", rel.feature_id)
                return None
            return [rel.instance - 1]
        if n == 1 or has_pattern:
            return [0]
        raw = str((rel.evidence or {}).get("raw_text") or "").upper()
        if "TYP" in raw or rel.raw.get("applies_to") == "all":
            return list(range(n))
        self.ctx.add("REVIEW_REQUIRED", "relationships", "AMBIGUOUS_INSTANCE",
                     f"{rel.feature_id} has {n} instances but '{rel.describe()}' does not say which one.",
                     rel.feature_id, relationship=rel.raw)
        return None

    def ref_instance(self, rel: FeatureRelationship) -> Optional[int]:
        ref = self.feats.get(rel.reference_feature or "")
        if ref is None:
            self.ctx.add("REVIEW_REQUIRED", "relationships", "UNSUPPORTED_REFERENCE",
                         f"{rel.feature_id}: reference {rel.reference_feature} is not a positioned feature.",
                         rel.feature_id)
            return None
        if rel.reference_instance is not None:
            return rel.reference_instance - 1
        if _n_instances(ref) == 1:
            return 0
        self.ctx.add("REVIEW_REQUIRED", "relationships", "AMBIGUOUS_INSTANCE",
                     f"{rel.feature_id}: reference {ref.id} has several instances; which one is not stated.",
                     rel.feature_id)
        return None

    # -- one relationship ------------------------------------------------------------------------
    def apply(self, rel: FeatureRelationship, inst: int) -> str:
        """Return 'done', 'wait' (dependency not solved yet) or 'skip' (reported)."""
        f = self.feats[rel.feature_id]
        v = rel.value.value if rel.value else None
        src = rel.describe()
        k = rel.kind
        if k in ("from_left", "from_right", "from_bottom", "from_top"):
            span = self.L if k in ("from_left", "from_right") else self.W
            if span is None:
                self.ctx.add("REVIEW_REQUIRED", "relationships", "BASE_SIZE_UNKNOWN",
                             f"{f.id}: {k} needs the base size, which is missing.", f.id)
                return "skip"
            off = _target_offset(f, rel, self.ctx)
            if off is None or v is None:
                return "skip"
            pos = v + off if k in ("from_left", "from_bottom") else span - v - off
            self.put(f.id, inst, rel.axis or "x", pos, src)
            return "done"
        if k == "from_base_bottom":
            self.ctx.add("REVIEW_REQUIRED", "relationships", "HORIZONTAL_FEATURE_UNSUPPORTED",
                         f"{f.id}: a height position implies a side (horizontal-axis) feature; not supported in POC.",
                         f.id, relationship=rel.raw)
            return "skip"
        if k in ("centered_horizontal", "centered_vertical", "centered_on_base", "symmetric"):
            axes = {"centered_horizontal": ["x"], "centered_vertical": ["y"]}.get(k, [rel.axis] if rel.axis else ["x", "y"])
            for a in axes:
                span = self.L if a == "x" else self.W
                if span is None:
                    self.ctx.add("REVIEW_REQUIRED", "relationships", "BASE_SIZE_UNKNOWN",
                                 f"{f.id}: centring needs the base size, which is missing.", f.id)
                    return "skip"
                self.put(f.id, inst, a, span / 2, src)
            return "done"
        if k in ("concentric", "aligned_with", "center_to_center"):
            ri = self.ref_instance(rel)
            if ri is None:
                return "skip"
            axes = ["x", "y"] if k == "concentric" else [rel.axis or "x"]
            refs = [self.known(rel.reference_feature or "", ri, a) for a in axes]
            if any(r is None for r in refs):
                return "wait"
            if k == "center_to_center":
                sign = rel.direction or self._infer_sign(rel, inst, ri)
                if sign is None or v is None:
                    return "skip"
                self.put(f.id, inst, axes[0], refs[0] + sign * v, src)  # type: ignore[operator]
            else:
                for a, r in zip(axes, refs):
                    self.put(f.id, inst, a, r, src)  # type: ignore[arg-type]
            return "done"
        return "skip"

    def _infer_sign(self, rel: FeatureRelationship, inst: int, ref_inst: int) -> Optional[int]:
        """Stage 1 numbers instances left-to-right, then top-to-bottom."""
        if rel.reference_feature == rel.feature_id and inst != ref_inst:
            sign = 1 if inst > ref_inst else -1
            if rel.axis == "y":
                sign = -sign
            self.ctx.add("WARNING", "relationships", "DIRECTION_FROM_INSTANCE_ORDER",
                         f"{rel.feature_id}#{inst + 1}: offset direction taken from instance numbering.",
                         rel.feature_id)
            return sign
        self.ctx.add("REVIEW_REQUIRED", "relationships", "DIRECTION_UNKNOWN",
                     f"{rel.feature_id}: '{rel.describe()}' does not say in which direction the offset goes.",
                     rel.feature_id, relationship=rel.raw)
        return None

    GRID_KINDS = ("from_left", "from_right", "from_bottom", "from_top", "pattern_spacing")

    def grid(self, fid: str, rels: list[FeatureRelationship]) -> list[FeatureRelationship]:
        """Place an nX feature from dimensions that apply to the group (edge offset + spacing per axis).

        Drafting convention: '4X Ø12' with '20' from an edge and '80' between centres on each axis is a grid.
        The grid is accepted only if it yields exactly n positions that all lie on the part; otherwise the
        dimensions most likely belong to something else and the reviewer is asked. Returns the relationships
        still to be processed individually."""
        f = self.feats[fid]
        n = len(self.cand[fid])
        group = [r for r in rels if r.instance is None and r.kind in self.GRID_KINDS]
        if n < 2 or not group or any(r.kind in ("linear_pattern", "circular_pattern") for r in rels):
            return rels
        if not any(r.kind == "pattern_spacing" for r in group) and len({r.axis for r in group}) < 2:
            return rels
        rest = [r for r in rels if r not in group]
        axes: dict[str, list[float]] = {}
        why = ""
        for axis, span in (("x", self.L), ("y", self.W)):
            edges = [r for r in group if r.axis == axis and r.kind != "pattern_spacing"]
            steps = [r.value.value for r in group if r.axis == axis and r.kind == "pattern_spacing"
                     and r.value and r.value.value is not None]
            if span is None:
                why = "base size unknown"
                break
            coords: list[float] = []
            for e in edges:
                off = _target_offset(f, e, self.ctx)
                if off is None or e.value is None or e.value.value is None:
                    continue
                v = e.value.value
                coords.append(v + off if e.kind in ("from_left", "from_bottom") else span - v - off)
            if steps:
                if not edges:
                    why = f"spacing along {axis} but no edge dimension to start from"
                    break
                start = edges[0]
                sign = 1 if start.kind in ("from_left", "from_bottom") else -1
                chain = [coords[0]]
                for s in steps:
                    chain.append(chain[-1] + sign * s)
                for extra in coords[1:]:  # an opposite-edge dimension must close the chain
                    if not any(close(extra, c) for c in chain):
                        why = f"the {axis} dimensions do not close: {chain} vs {extra:g}"
                coords = chain
            axes[axis] = sorted({round(c, 6) for c in coords})
        xs, ys = axes.get("x", []), axes.get("y", [])
        if not why and (not xs or not ys):
            why = "the group dimensions fix only one axis"
        if not why and len(xs) * len(ys) != n:
            why = f"{len(xs)} x {len(ys)} positions from the dimensions, but the feature has {n}"
        pts = [(x, y) for y in sorted(ys, reverse=True) for x in xs]  # Stage 1 order: left-right, top-bottom
        if not why:
            for x, y in pts:
                h = _half_extent(f, "x") or 0.0
                v = _half_extent(f, "y") or 0.0
                if x - h < -1e-6 or x + h > self.L + 1e-6 or y - v < -1e-6 or y + v > self.W + 1e-6:  # type: ignore[operator]
                    why = f"it would put a copy at ({x:g}, {y:g}), off the part; the dimensions probably locate another feature"
                    break
        src = "; ".join(r.describe() for r in group)
        if why:
            self.ctx.add("REVIEW_REQUIRED", "relationships", "PATTERN_NOT_A_GRID",
                         f"{fid}: cannot place its {n} copies from the group dimensions ({src}): {why}.", fid,
                         dimensions=[r.describe() for r in group])
            return rest
        for i, (x, y) in enumerate(pts):
            self.put(fid, i, "x", x, f"grid from {src}")
            self.put(fid, i, "y", y, f"grid from {src}")
        self.ctx.add("WARNING", "relationships", "PATTERN_FROM_DIMENSIONS",
                     f"{fid}: {n} copies placed as a {len(xs)} x {len(ys)} grid from its group dimensions.", fid)
        return rest

    def expand_patterns(self, patterns: list[FeatureRelationship]) -> None:
        for rel in patterns:
            f = self.feats[rel.feature_id]
            n = len(self.cand[f.id])
            count = min(rel.count or n, n)
            if rel.kind == "linear_pattern":
                x0, y0 = self.known(f.id, 0, "x"), self.known(f.id, 0, "y")
                if x0 is None or y0 is None or rel.direction is None:
                    self.ctx.add("REVIEW_REQUIRED", "relationships", "INCOMPLETE_PATTERN",
                                 f"{f.id}: linear pattern needs a located first instance and a direction.", f.id)
                    continue
                step = rel.direction * (rel.value.value if rel.value and rel.value.value else 0)
                for i in range(1, count):
                    self.put(f.id, i, "x", x0 + (step * i if rel.axis == "x" else 0), rel.describe())
                    self.put(f.id, i, "y", y0 + (step * i if rel.axis == "y" else 0), rel.describe())
            elif rel.kind == "circular_pattern":
                if rel.reference_feature:
                    ri = self.ref_instance(rel)
                    cx = self.known(rel.reference_feature, ri, "x") if ri is not None else None
                    cy = self.known(rel.reference_feature, ri, "y") if ri is not None else None
                else:
                    cx, cy = (self.L / 2 if self.L else None), (self.W / 2 if self.W else None)
                    self.ctx.add("WARNING", "relationships", "PATTERN_CENTER_BASE",
                                 f"{f.id}: circular pattern centre taken as the base centre.", f.id)
                if cx is None or cy is None or not rel.value or rel.value.value is None or not rel.angle:
                    self.ctx.add("REVIEW_REQUIRED", "relationships", "INCOMPLETE_PATTERN",
                                 f"{f.id}: circular pattern centre/diameter unknown.", f.id)
                    continue
                r = rel.value.value / 2
                for i in range(count):
                    a = math.radians((rel.angle.value or 0) + 360.0 * i / count)
                    self.put(f.id, i, "x", cx + r * math.cos(a), rel.describe())
                    self.put(f.id, i, "y", cy + r * math.sin(a), rel.describe())

    # -- driver ----------------------------------------------------------------------------------
    def run(self) -> None:
        by_feature: dict[str, list[FeatureRelationship]] = {}
        for rel in self.d.relationships:
            if rel.feature_id in self.feats:
                by_feature.setdefault(rel.feature_id, []).append(rel)
            elif rel.kind not in ("coincident", "parallel", "perpendicular"):
                self.ctx.add("WARNING", "relationships", "RELATIONSHIP_NOT_USED",
                             f"{rel.feature_id}: relationships on this feature type are not used for positioning.",
                             rel.feature_id)
        pending: list[tuple[FeatureRelationship, int]] = []
        patterns: list[FeatureRelationship] = []
        for fid, rels in list(by_feature.items()):
            by_feature[fid] = rels = self.grid(fid, rels)
        for fid, rels in by_feature.items():
            has_pattern = any(r.kind in ("linear_pattern", "circular_pattern") for r in rels)
            for rel in rels:
                if rel.kind in ("linear_pattern", "circular_pattern"):
                    patterns.append(rel)
                elif rel.kind == "pattern_spacing":
                    self.ctx.add("REVIEW_REQUIRED", "relationships", "PATTERN_NOT_A_GRID",
                                 f"{fid}: spacing '{rel.describe()}' could not be applied to specific copies.", fid)
                elif rel.kind in ("coincident", "parallel", "perpendicular"):
                    self.ctx.trace.append(f"{fid}: {rel.kind} recorded (not a positioning constraint)")
                else:
                    for i in self.instances_for(rel, has_pattern) or []:
                        pending.append((rel, i))

        for _ in range(len(pending) + 2):  # fixed point over feature-to-feature references
            waiting = []
            for rel, i in pending:
                if self.apply(rel, i) == "wait":
                    waiting.append((rel, i))
            if len(waiting) == len(pending):
                break
            pending = waiting
            if patterns:
                self.expand_patterns(patterns)
                patterns = []
        if patterns:
            self.expand_patterns(patterns)
        for rel, i in pending:
            self.ctx.add("REVIEW_REQUIRED", "relationships", "UNRESOLVED_REFERENCE",
                         f"{rel.feature_id}#{i + 1}: depends on {rel.reference_feature}, whose position is unknown.",
                         rel.feature_id)
        self.finalize()

    def finalize(self) -> None:
        for fid, f in self.feats.items():
            positions: list[Position] = []
            for i, c in enumerate(self.cand[fid]):
                p = Position()
                conflicted = False
                for axis in ("x", "y"):
                    vals = c[axis]
                    if not vals:
                        continue
                    if all(close(v, vals[0][0]) for v, _ in vals):
                        setattr(p, axis, round(vals[0][0], 6))
                        setattr(p, f"{axis}_source", " | ".join(s for _, s in vals))
                    else:
                        conflicted = True
                        self.ctx.add("REVIEW_REQUIRED", "relationships", "OVERCONSTRAINED_CONFLICT",
                                     f"{fid}#{i + 1}: {axis} is constrained to different values.", fid,
                                     values=[{"value": v, "source": s} for v, s in vals])
                missing = [a for a in ("x", "y") if getattr(p, a) is None]
                if missing and not conflicted:
                    self.ctx.add("REVIEW_REQUIRED", "relationships", "INSUFFICIENT_CONSTRAINTS",
                                 f"{f.type.capitalize()} {fid}" + (f" instance {i + 1}" if len(self.cand[fid]) > 1 else "")
                                 + f" has no evidence-backed {' or '.join(missing).upper()} position.", fid,
                                 known={a: getattr(p, a) for a in ("x", "y") if getattr(p, a) is not None},
                                 missing=missing, instance=i + 1)
                if self.T is not None:
                    p.z, p.z_source = self.T, "top face of base"
                positions.append(p)
            f.instances = positions


def _place_boss_ribs(g: Gusset, boss: Boss, ctx: Context) -> None:
    """Radial ribs: the foot starts just inside the cylinder (where the rib's side faces meet it) so the
    rib fuses with the boss; run is measured from the boss surface."""
    if not g.directions or boss.diameter is None or boss.diameter.value is None or g.thickness is None \
            or g.thickness.value is None:
        return
    R, t = boss.diameter.value / 2, g.thickness.value
    if t / 2 >= R:
        return
    inset = math.sqrt(R * R - (t / 2) ** 2)
    delta = R - inset
    seg = R * R * math.acos(inset / R) - inset * math.sqrt(R * R - inset * inset)  # circle segment beyond the foot
    located = [p for p in boss.instances if p.x is not None and p.y is not None]
    if len(located) != 1:
        ctx.add("REVIEW_REQUIRED", "relationships", "GUSSET_POSITION_UNKNOWN",
                f"{g.id}: ribs need exactly one located boss ({boss.id}).", g.id)
        return
    p = located[0]
    placements = []
    for d in g.directions:
        a, sgn = d[1], (1 if d[0] == "+" else -1)
        other = "y" if a == "x" else "x"
        foot = getattr(p, a) + sgn * inset
        placements.append({"axis": a, "dir": sgn, f"{a}0": foot, f"{other}0": getattr(p, other), "z0": p.z,
                           "run_offset": delta, "overlap_area": seg})
        ctx.trace.append(f"{g.id}: rib from {boss.id} towards {d}, foot {a}={foot:g}")
    g.placements = placements
    g.instances = [Position(x=pl.get("x0"), y=pl.get("y0"), z=pl.get("z0"), x_source=f"surface of {boss.id}",
                            y_source=f"axis of {boss.id}") for pl in placements]


def place_gussets(drawing: Drawing, ctx: Context) -> None:
    """A gusset sits against a face of each instance of its lug. Only evidence-backed placement is used:
    the side is stated, and it is centred across the lug face only when centered_on_lug is True."""
    for g in drawing.features:
        if not isinstance(g, Gusset):
            continue
        host = drawing.feature(g.attached_to or "")
        if isinstance(host, Boss):
            _place_boss_ribs(g, host, ctx)
            continue
        lug = host
        if not isinstance(lug, Lug) or lug.normal_axis is None or g.side is None or g.centered_on_lug is not True \
                or lug.thickness is None or lug.thickness.value is None:
            continue  # already reported by the dimension validator
        a = lug.normal_axis
        other = "y" if a == "x" else "x"
        located = [p for p in lug.instances if p.x is not None and p.y is not None]
        if not located or len(located) != len(lug.instances):
            ctx.add("REVIEW_REQUIRED", "relationships", "GUSSET_POSITION_UNKNOWN",
                    f"{g.id}: lug {lug.id} is not fully located.", g.id)
            continue
        mid = sum(getattr(p, a) for p in located) / len(located)
        placements: list[dict] = []
        for p in located:
            c = getattr(p, a)
            if g.side in ("outer", "inner"):
                if len(located) < 2 or abs(c - mid) < 1e-9:
                    ctx.add("REVIEW_REQUIRED", "relationships", "GUSSET_SIDE_AMBIGUOUS",
                            f"{g.id}: 'outer/inner' needs lugs on both sides of their midpoint.", g.id)
                    placements = []
                    break
                d = 1 if c > mid else -1
                d = d if g.side == "outer" else -d
            else:
                d = 1 if g.side == "plus" else -1
            foot = c + d * lug.thickness.value / 2
            placements.append({"axis": a, "dir": d, f"{a}0": foot, f"{other}0": getattr(p, other), "z0": p.z})
            ctx.trace.append(f"{g.id}: on {lug.id} {g.side} face, foot {a}={foot:g}, {other}={getattr(p, other):g}, "
                             f"dir {d:+d}")
        g.placements = placements
        g.instances = [Position(x=pl.get("x0"), y=pl.get("y0"), z=pl.get("z0"),
                                x_source=f"face of {lug.id}", y_source=f"face of {lug.id}") for pl in placements]


def solve(drawing: Drawing, ctx: Context) -> None:
    _Solver(drawing, ctx).run()
    place_gussets(drawing, ctx)
    solved = sum(1 for f in drawing.features for p in f.instances if p.x is not None and p.y is not None)
    log.info("Coordinate solver: %d instance position(s) solved; status %s",
             solved, ctx.stage_status("relationships").value)
