"""Compare what the CAD tool actually built with what the drawing says.

Inputs: validated_geometry.json (drawing values + evidence), cad_plan.json (analytic expectations)
and the measured model from the CAD backend (volume, exact bbox, vertical cylinders, bodies).
Every check carries the drawing evidence it traces back to.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

log = logging.getLogger("stage5.verifier")

LEN_TOL = 0.01          # mm
VOL_REL_TOL = 1e-3      # 0.1 %


_OVERALL = {"length": "overall.length", "width": "overall.width", "thickness": "overall.height"}


def _evidence(validated: dict, field: str) -> Optional[str]:
    """Drawing evidence for a field; base sizes may have come from overall_dimensions."""
    candidates = [field]
    if field.split(".")[-1] in _OVERALL:
        candidates.append(_OVERALL[field.split(".")[-1]])
    for want in candidates:
        for m in validated.get("measurements", []):
            if m.get("field") == want and m.get("evidence"):
                ev = m["evidence"]
                return f"'{ev.get('raw_text')}' ({ev.get('view')})"
    return None


class _Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, category: str, name: str, ok: bool, expected: Any = None, measured: Any = None,
            evidence: Optional[str] = None, severity: str = "FAIL") -> None:
        self.checks.append({"category": category, "check": name, "ok": bool(ok), "expected": expected,
                            "measured": measured, "drawing_evidence": evidence,
                            "severity_if_failed": severity})


def _r(v: Any) -> Any:
    return round(v, 3) if isinstance(v, float) else v


def verify_model(validated: dict, plan: dict, measured: Optional[dict]) -> dict[str, Any]:
    rep = _Report()
    base = validated.get("base") or {}
    exp = plan.get("expected", {})

    # -- completeness: does the model contain everything the drawing describes? -------------------
    skipped = plan.get("skipped", [])
    rep.add("completeness", "validation status of drawing geometry", validated.get("status") == "PASS",
            "PASS", validated.get("status"), severity="REVIEW_REQUIRED")
    rep.add("completeness", "all drawing features modelled", not skipped, "0 skipped",
            [f"{s['feature_id']}: {s['reason']}" for s in skipped] or 0, severity="REVIEW_REQUIRED")

    if measured is None:
        rep.add("geometry_validity", "model measurements available", False, "measurements", None,
                severity="REVIEW_REQUIRED")
        return _finish(rep, validated, plan, measured)

    # -- validity ---------------------------------------------------------------------------------
    rep.add("geometry_validity", "single solid body", measured.get("bodies") == 1, 1, measured.get("bodies"))
    rep.add("geometry_validity", "positive volume", (measured.get("volume_mm3") or 0) > 0, "> 0",
            _r(measured.get("volume_mm3")))
    if measured.get("rebuild_errors") is not None:
        rep.add("geometry_validity", "no rebuild errors", measured["rebuild_errors"] == 0, 0, measured["rebuild_errors"])

    # -- overall / bounding dimensions ------------------------------------------------------------
    bb = measured.get("bbox")
    if bb:
        size = [bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]]
        for axis, i, key in (("length (X)", 0, "length"), ("width (Y)", 1, "width")):
            rep.add("overall_dimensions", f"overall {axis}", abs(size[i] - base.get(key, math.nan)) <= LEN_TOL,
                    base.get(key), _r(size[i]), _evidence(validated, f"{base.get('id')}.{key}"))
        eb = exp.get("bbox")
        if eb:
            rep.add("overall_dimensions", "overall height (Z)", abs(size[2] - (eb[5] - eb[2])) <= LEN_TOL,
                    _r(eb[5] - eb[2]), _r(size[2]))
            rep.add("bounding_box", "model bounding box at origin", all(abs(a - b) <= LEN_TOL for a, b in zip(eb, bb)),
                    [_r(v) for v in eb], [_r(v) for v in bb])

    # -- thickness --------------------------------------------------------------------------------
    base_ext = next((o for o in plan.get("operations", []) if o.get("operation") == "extrude"), None)
    rep.add("thickness", "base extrusion equals drawing thickness",
            base_ext is not None and abs(base_ext["distance"] - base.get("thickness", math.nan)) <= LEN_TOL,
            base.get("thickness"), base_ext and base_ext["distance"], _evidence(validated, f"{base.get('id')}.thickness"))

    # -- volume -----------------------------------------------------------------------------------
    ev, mv = exp.get("volume_mm3"), measured.get("volume_mm3")
    if ev and mv is not None:
        rep.add("volume", "volume matches analytic model", abs(ev - mv) <= VOL_REL_TOL * ev, _r(ev), _r(mv))

    # -- holes / bosses / slot ends / fillets: positions and diameters ----------------------------
    cyls = measured.get("cylinders") or []
    feats = {f["id"]: f for f in validated.get("features", [])}
    for c in exp.get("cylinders", []):
        hit = next((m for m in cyls if m.get("axis", "z") == c.get("axis", "z")
                    and math.dist(c["center"], m["center"]) <= LEN_TOL
                    and abs(c["radius"] - m["radius"]) <= LEN_TOL), None)
        f = feats.get(c["feature_id"], {})
        raw = (f.get("evidence") or {}).get("raw_text")
        label = {"hole": "hole", "boss": "boss", "slot_end": "slot end arc", "fillet": "corner fillet",
                 "lug_hole": "lug cross hole", "lug_top": "lug top arc"}.get(c.get("kind"), "cylinder")
        plane = {"z": "x, y", "x": "y, z", "y": "x, z"}[c.get("axis", "z")]
        rep.add("hole_positions" if c.get("kind") in ("hole", "lug_hole") else "feature_positions",
                f"{label} {c['feature_id']} Ø{2 * c['radius']:g} at ({plane}) = ({c['center'][0]:g}, {c['center'][1]:g})",
                hit is not None, {"center": c["center"], "diameter": 2 * c["radius"]},
                hit and {"center": [_r(v) for v in hit["center"]], "diameter": _r(2 * hit["radius"])},
                f"'{raw}'" if raw else None)

    # -- feature count ----------------------------------------------------------------------------
    def distinct(items: list[dict]) -> set[tuple]:
        return {(i.get("axis", "z"), round(i["center"][0], 2), round(i["center"][1], 2), round(i["radius"], 2))
                for i in items}
    want, got = distinct(exp.get("cylinders", [])), distinct(cyls)
    rep.add("feature_count", "cylindrical features (holes, bosses, slot ends, fillets, lugs)", want == got,
            len(want), len(got))
    extra = sorted(got - want)
    if extra:
        rep.add("feature_count", "no unexpected cylindrical features", False, [],
                [{"axis": a, "center": [x, y], "diameter": 2 * r} for a, x, y, r in extra])
    n_holes_drawing = sum(len(f.get("instances", [])) for f in validated.get("features", []) if f["type"] == "hole")
    n_holes_model = len({(round(c["center"][0], 2), round(c["center"][1], 2)) for c in exp.get("cylinders", [])
                         if c.get("kind") == "hole"
                         and any(m.get("axis", "z") == "z" and math.dist(c["center"], m["center"]) <= LEN_TOL
                                 for m in cyls)})
    skipped_ids = {s["feature_id"] for s in plan.get("skipped", [])}
    n_skipped = sum(len(f.get("instances", [])) for f in validated.get("features", [])
                    if f["type"] == "hole" and f["id"] in skipped_ids)
    explained = n_holes_drawing - n_holes_model == n_skipped and n_skipped > 0
    rep.add("feature_count", "hole count (drawing vs model)", n_holes_drawing == n_holes_model,
            n_holes_drawing, n_holes_model, severity="REVIEW_REQUIRED" if explained else "FAIL")
    return _finish(rep, validated, plan, measured)


def _finish(rep: _Report, validated: dict, plan: dict, measured: Optional[dict]) -> dict[str, Any]:
    failed = [c for c in rep.checks if not c["ok"]]
    if any(c["severity_if_failed"] == "FAIL" for c in failed):
        status = "FAIL"
    elif failed:
        status = "REVIEW_REQUIRED"
    else:
        status = "PASS"
    by_cat: dict[str, dict[str, int]] = {}
    for c in rep.checks:
        d = by_cat.setdefault(c["category"], {"passed": 0, "failed": 0})
        d["passed" if c["ok"] else "failed"] += 1
    log.info("Stage 5 verification: %s (%d checks, %d failed)", status, len(rep.checks), len(failed))
    for c in failed:
        log.warning("  FAILED [%s] %s: expected %s, measured %s", c["category"], c["check"], c["expected"], c["measured"])
    return {"status": status, "part_name": plan.get("part_name"), "summary": by_cat,
            "checks": rep.checks, "measured": measured}
