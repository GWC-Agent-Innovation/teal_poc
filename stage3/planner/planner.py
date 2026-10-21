"""3A: validated_geometry.json -> ordered, deterministic CAD operations.

Order: base -> bosses -> cuts (holes, slots, pockets, cutouts) -> edge treatments.
Sketches sit on the XY plane at an explicit Z offset; through cuts go both ways from Z=0
so they always pass the full body. The planner also records analytic expectations
(volume, bounding box, cylinder axes) so the executor can verify the built model.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from .operation_models import CADPlan

log = logging.getLogger("stage3.planner")


class PlanningError(Exception):
    def __init__(self, code: str, message: str, feature_id: Optional[str] = None):
        super().__init__(message)
        self.code, self.message, self.feature_id = code, message, feature_id

    def as_dict(self) -> dict[str, Any]:
        return {"status": "FAIL", "stage": "cad_planner", "code": self.code, "message": self.message,
                "feature_id": self.feature_id}


class _Builder:
    def __init__(self) -> None:
        self.ops: list[dict[str, Any]] = []

    def add(self, operation: str, **fields: Any) -> str:
        op_id = f"OP{len(self.ops) + 1:03d}"
        self.ops.append({"id": op_id, "operation": operation, **fields})
        return op_id

    def sketch(self, offset: float = 0.0, feature_id: Optional[str] = None) -> str:
        return self.add("create_sketch", plane="XY", offset=offset, feature_id=feature_id)


def _located(f: dict) -> list[dict]:
    return [p for p in f.get("instances", []) if p.get("x") is not None and p.get("y") is not None]


def _complete(f: dict, fields: tuple[str, ...]) -> Optional[str]:
    missing = [k for k in fields if f.get(k) is None]
    if missing:
        return f"missing {', '.join(missing)}"
    inst = f.get("instances", [])
    if "instances" in f and (not inst or len(_located(f)) != len(inst)):
        return "position not fully solved"
    return None


def _lug_profile(b: _Builder, s: str, fid: str, uc: float, z0: float, w: float, h: float, r: Optional[float]) -> float:
    """Draw the lug outline in its plane (u = in-plane horizontal, v = Z). Returns the profile area."""
    left, right, top = uc - w / 2, uc + w / 2, z0 + h
    if r is None or r <= 0:
        b.add("rectangle", sketch=s, origin=(left, z0), length=w, width=h, feature_id=fid)
        return w * h
    if abs(r - w / 2) < 1e-9:  # full round top
        b.add("line", sketch=s, start=(left, z0), end=(right, z0), feature_id=fid)
        b.add("line", sketch=s, start=(right, z0), end=(right, top - r), feature_id=fid)
        b.add("arc", sketch=s, center=(uc, top - r), start=(right, top - r), end=(left, top - r), direction="ccw",
              feature_id=fid)
        b.add("line", sketch=s, start=(left, top - r), end=(left, z0), feature_id=fid)
        return w * (h - r) + math.pi * r * r / 2
    b.add("line", sketch=s, start=(left, z0), end=(right, z0), feature_id=fid)
    b.add("line", sketch=s, start=(right, z0), end=(right, top - r), feature_id=fid)
    b.add("arc", sketch=s, center=(right - r, top - r), start=(right, top - r), end=(right - r, top), direction="ccw",
          feature_id=fid)
    b.add("line", sketch=s, start=(right - r, top), end=(left + r, top), feature_id=fid)
    b.add("arc", sketch=s, center=(left + r, top - r), start=(left + r, top), end=(left, top - r), direction="ccw",
          feature_id=fid)
    b.add("line", sketch=s, start=(left, top - r), end=(left, z0), feature_id=fid)
    return w * h - 2 * (r * r - math.pi * r * r / 4)


def _plan_lugs_and_gussets(b: _Builder, feats: list[dict], T: float, expected: dict, skip) -> None:
    hole_groups: dict[str, list[tuple[dict, dict]]] = {}
    for f in (x for x in feats if x["type"] == "lug"):
        why = _complete(f, ("thickness", "width", "height", "normal_axis"))
        if why:
            skip(f, why)
            continue
        a = f["normal_axis"]
        plane = "YZ" if a == "x" else "XZ"
        t, w, h, r = f["thickness"], f["width"], f["height"], f.get("top_radius")
        for p in _located(f):
            offset, uc = (p["x"], p["y"]) if a == "x" else (p["y"], p["x"])
            s = b.add("create_sketch", plane=plane, offset=offset, feature_id=f["id"])
            area = _lug_profile(b, s, f["id"], uc, T, w, h, r)
            b.add("extrude", profile=s, distance=t, symmetric=True, feature_id=f["id"])
            expected["volume_mm3"] += area * t
            expected["bbox"][5] = max(expected["bbox"][5], T + h)
            if r:
                arcs = [(uc, T + h - r)] if abs(r - w / 2) < 1e-9 else [(uc + w / 2 - r, T + h - r), (uc - w / 2 + r, T + h - r)]
                for c in arcs:
                    expected["cylinders"].append({"feature_id": f["id"], "kind": "lug_top", "axis": a,
                                                  "center": list(c), "radius": r})
            if f.get("hole_diameter") and f.get("hole_height") is not None:
                hole_groups.setdefault(a, []).append((f, p))
    for a, items in hole_groups.items():
        s = b.add("create_sketch", plane="YZ" if a == "x" else "XZ", offset=0.0, feature_id="lug_holes")
        drawn: set[tuple[float, float, float]] = set()
        for f, p in items:
            uc = p["y"] if a == "x" else p["x"]
            c = (uc, T + f["hole_height"])
            key = (round(c[0], 6), round(c[1], 6), round(f["hole_diameter"], 6))
            if key not in drawn:  # coaxial lugs (a clevis) share one circle; the through cut pierces both
                drawn.add(key)
                b.add("circle", sketch=s, center=c, diameter=f["hole_diameter"], feature_id=f["id"])
            expected["volume_mm3"] -= math.pi * (f["hole_diameter"] / 2) ** 2 * f["thickness"]
            expected["cylinders"].append({"feature_id": f["id"], "kind": "lug_hole", "axis": a, "center": list(c),
                                          "radius": f["hole_diameter"] / 2})
        b.add("cut_extrude", profile=s, extent="through_all_both", feature_id="lug_holes")
    for g in (x for x in feats if x["type"] == "gusset"):
        why = _complete(g, ("rise", "run", "thickness")) or (None if g.get("placements") else "placement not solved")
        if why:
            skip(g, why)
            continue
        for pl in g["placements"]:
            a = pl["axis"]
            other = "y" if a == "x" else "x"
            foot, d = pl[f"{a}0"], pl["dir"]
            run = g["run"] + pl.get("run_offset", 0.0)  # ribs on a boss start slightly inside the cylinder
            s = b.add("create_sketch", plane="XZ" if a == "x" else "YZ", offset=pl[f"{other}0"], feature_id=g["id"])
            p0, p1, p2 = (foot, T), (foot + d * run, T), (foot, T + g["rise"])
            for start, end in ((p0, p1), (p1, p2), (p2, p0)):
                b.add("line", sketch=s, start=start, end=end, feature_id=g["id"])
            b.add("extrude", profile=s, distance=g["thickness"], symmetric=True, feature_id=g["id"])
            expected["volume_mm3"] += 0.5 * run * g["rise"] * g["thickness"] - g["rise"] * pl.get("overlap_area", 0.0)


def plan(validated: dict[str, Any], allow_override: bool = False) -> CADPlan:
    status = validated.get("status")
    if status != "PASS" and not allow_override:
        raise PlanningError("VALIDATION_NOT_PASSED",
                            f"validated geometry status is {status}; CAD planning requires PASS "
                            "(use the development override to plan anyway).")
    base = validated.get("base") or {}
    L, W, T = base.get("length"), base.get("width"), base.get("thickness")
    if not all(isinstance(v, (int, float)) and v > 0 for v in (L, W, T)):
        raise PlanningError("BASE_UNDEFINED", f"base size is incomplete: {L} x {W} x {T}", base.get("id"))

    b = _Builder()
    skipped: list[dict] = []
    expected = {"volume_mm3": L * W * T, "bbox": [0.0, 0.0, 0.0, L, W, T], "cylinders": []}
    feats = validated.get("features", [])

    # 1. base
    s = b.sketch(0.0, base.get("id"))
    b.add("rectangle", sketch=s, origin=(0.0, 0.0), length=L, width=W, feature_id=base.get("id"))
    base_op = b.add("extrude", profile=s, distance=T, direction="normal", feature_id=base.get("id"))

    def skip(f: dict, reason: str) -> None:
        skipped.append({"feature_id": f["id"], "type": f["type"], "reason": reason})
        log.warning("Skipping %s (%s): %s", f["id"], f["type"], reason)

    # 2. bosses (grouped by height so each group is one sketch + extrude)
    boss_tops: dict[tuple[float, float], float] = {}
    groups: dict[float, list[tuple[dict, dict]]] = {}
    for f in feats:
        if f["type"] == "boss":
            why = _complete(f, ("diameter", "height"))
            if why:
                skip(f, why)
                continue
            for p in _located(f):
                groups.setdefault(f["height"], []).append((f, p))
    for height, items in sorted(groups.items()):
        s = b.sketch(T, items[0][0]["id"])
        for f, p in items:
            b.add("circle", sketch=s, center=(p["x"], p["y"]), diameter=f["diameter"], feature_id=f["id"])
            boss_tops[(round(p["x"], 6), round(p["y"], 6))] = T + height
            expected["volume_mm3"] += math.pi * (f["diameter"] / 2) ** 2 * height
            expected["cylinders"].append({"feature_id": f["id"], "kind": "boss", "center": [p["x"], p["y"]],
                                          "radius": f["diameter"] / 2})
            expected["bbox"][5] = max(expected["bbox"][5], T + height)
        b.add("extrude", profile=s, distance=height, direction="normal", feature_id=items[0][0]["id"])

    # 2b. lugs (upright plates) and their cross holes, then gussets
    _plan_lugs_and_gussets(b, feats, T, expected, skip)

    # 3. cuts
    through_items: list[tuple[str, dict, dict]] = []
    for f in feats:
        t = f["type"]
        if t == "hole":
            why = _complete(f, ("diameter",)) or (None if f.get("through") or f.get("depth") else "depth unknown")
            if why:
                skip(f, why)
                continue
            for p in _located(f):
                top = boss_tops.get((round(p["x"], 6), round(p["y"], 6)), T)
                r = f["diameter"] / 2
                expected["cylinders"].append({"feature_id": f["id"], "kind": "hole", "center": [p["x"], p["y"]], "radius": r})
                if f.get("through"):
                    through_items.append(("circle", f, p))
                    expected["volume_mm3"] -= math.pi * r * r * top
                else:
                    s = b.sketch(top, f["id"])
                    b.add("circle", sketch=s, center=(p["x"], p["y"]), diameter=f["diameter"], feature_id=f["id"])
                    b.add("cut_extrude", profile=s, extent="blind", distance=f["depth"], direction="reverse",
                          feature_id=f["id"])
                    expected["volume_mm3"] -= math.pi * r * r * f["depth"]
        elif t == "slot":
            why = _complete(f, ("length", "width", "orientation")) or \
                (None if f.get("through") or f.get("depth") else "depth unknown")
            if why:
                skip(f, why)
                continue
            area = (f["length"] - f["width"]) * f["width"] + math.pi * (f["width"] / 2) ** 2
            for p in _located(f):
                d = (f["length"] - f["width"]) / 2
                ends = [(p["x"] - d, p["y"]), (p["x"] + d, p["y"])] if f["orientation"] == "along_x" else                     [(p["x"], p["y"] - d), (p["x"], p["y"] + d)]
                for c in ends:
                    expected["cylinders"].append({"feature_id": f["id"], "kind": "slot_end", "center": list(c),
                                                  "radius": f["width"] / 2})
                if f.get("through"):
                    through_items.append(("slot", f, p))
                    expected["volume_mm3"] -= area * T
                else:
                    s = b.sketch(T, f["id"])
                    b.add("slot", sketch=s, center=(p["x"], p["y"]), length=f["length"], width=f["width"],
                          angle=0.0 if f["orientation"] == "along_x" else 90.0, feature_id=f["id"])
                    b.add("cut_extrude", profile=s, extent="blind", distance=f["depth"], direction="reverse",
                          feature_id=f["id"])
                    expected["volume_mm3"] -= area * f["depth"]
        elif t in ("pocket", "cutout"):
            fields = ("length", "width", "depth") if t == "pocket" else ("length", "width")
            why = _complete(f, fields) or ("corner radius not supported in POC" if f.get("corner_radius") else None)
            if why:
                skip(f, why)
                continue
            for p in _located(f):
                origin = (p["x"] - f["length"] / 2, p["y"] - f["width"] / 2)
                if t == "cutout":
                    through_items.append(("rect", f, p))
                    expected["volume_mm3"] -= f["length"] * f["width"] * T
                else:
                    s = b.sketch(T, f["id"])
                    b.add("rectangle", sketch=s, origin=origin, length=f["length"], width=f["width"], feature_id=f["id"])
                    b.add("cut_extrude", profile=s, extent="blind", distance=f["depth"], direction="reverse",
                          feature_id=f["id"])
                    expected["volume_mm3"] -= f["length"] * f["width"] * f["depth"]
        elif t == "unsupported":
            skip(f, f"feature type '{f.get('stage1_type')}' not supported")

    if through_items:
        s = b.sketch(0.0, "through_cuts")
        for kind, f, p in through_items:
            if kind == "circle":
                b.add("circle", sketch=s, center=(p["x"], p["y"]), diameter=f["diameter"], feature_id=f["id"])
            elif kind == "slot":
                b.add("slot", sketch=s, center=(p["x"], p["y"]), length=f["length"], width=f["width"],
                      angle=0.0 if f["orientation"] == "along_x" else 90.0, feature_id=f["id"])
            else:
                b.add("rectangle", sketch=s, origin=(p["x"] - f["length"] / 2, p["y"] - f["width"] / 2),
                      length=f["length"], width=f["width"], feature_id=f["id"])
        b.add("cut_extrude", profile=s, extent="through_all_both", feature_id="through_cuts")

    # 4. edge treatments on the base's vertical corner edges
    corner_lines = [((x, y, 0.0), (x, y, T)) for x, y in ((0.0, 0.0), (L, 0.0), (0.0, W), (L, W))]
    for f in feats:
        if f["type"] not in ("fillet", "chamfer"):
            continue
        size_key = "radius" if f["type"] == "fillet" else "distance"
        why = _complete(f, (size_key,)) or (None if f.get("edges") == "base_vertical_corners"
                                            else f"edge set '{f.get('edges')}' not supported")
        if why:
            skip(f, why)
            continue
        if f["type"] == "fillet":
            r = f["radius"]
            b.add("fillet", target=base_op, edges="base_vertical_corners", edge_lines=corner_lines, radius=r,
                  feature_id=f["id"])
            expected["volume_mm3"] -= 4 * (r * r - math.pi * r * r / 4) * T
            for cx, cy in ((r, r), (L - r, r), (r, W - r), (L - r, W - r)):
                expected["cylinders"].append({"feature_id": f["id"], "kind": "fillet", "center": [cx, cy], "radius": r})
        else:
            d, a = f["distance"], f.get("angle") or 45.0
            b.add("chamfer", target=base_op, edges="base_vertical_corners", edge_lines=corner_lines, distance=d,
                  angle=a, feature_id=f["id"])
            expected["volume_mm3"] -= 4 * 0.5 * d * d * math.tan(math.radians(a)) * T

    cad_plan = CADPlan(part_name=str(validated.get("part_name") or "part"), source_status=status,
                       development_override=allow_override and status != "PASS",
                       operations=b.ops, expected=expected, skipped=skipped)  # type: ignore[arg-type]
    log.info("CAD plan: %d operation(s), %d skipped feature(s)", len(cad_plan.operations), len(skipped))
    return cad_plan
