"""Build validated_geometry.json and validation_report.json payloads."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from .models import STAGES, Context, Drawing, Measurement


def _mm(m: Optional[Measurement]) -> Optional[float]:
    return m.value if m is not None else None


_FIELDS = {
    "hole": ("diameter", "depth"),
    "slot": ("length", "width", "end_radius", "depth"),
    "pocket": ("length", "width", "depth", "corner_radius"),
    "cutout": ("length", "width", "corner_radius"),
    "boss": ("diameter", "height"),
    "lug": ("thickness", "width", "height", "hole_diameter", "hole_height"),
    "gusset": ("rise", "run", "thickness"),
    "fillet": ("radius",),
    "chamfer": ("distance", "angle"),
}


def _feature(f) -> dict[str, Any]:
    out: dict[str, Any] = {"id": f.id, "type": f.type, "stage1_type": f.stage1_type}
    if f.type == "unsupported":
        return out
    out["quantity"] = f.quantity
    for field in _FIELDS[f.type]:
        out[field] = _mm(getattr(f, field))
    for flag in ("through", "orientation", "edges", "normal_axis", "top_type", "attached_to", "side", "centered_on_lug",
                 "directions"):
        if hasattr(f, flag):
            out[flag] = getattr(f, flag)
    if f.type == "lug":
        from .dimension_validator import lug_top_radius
        out["top_radius"] = lug_top_radius(f)
    if f.type == "gusset":
        out["placements"] = f.placements
    if getattr(f, "thread", None) is not None:
        out["thread"] = {"designation": f.thread.designation, "modelled": False}
    if f.instances:
        out["instances"] = [p.model_dump() for p in f.instances]
    out["evidence"] = f.evidence
    return out


def build_validated(drawing: Optional[Drawing], ctx: Context, source: str) -> dict[str, Any]:
    status = ctx.overall_status()
    base = None
    if drawing is not None and drawing.base is not None:
        b = drawing.base
        base = {"id": b.id, "type": b.shape, "length": _mm(b.length), "width": _mm(b.width),
                "thickness": _mm(b.thickness)}
    issues = [i.model_dump() for i in ctx.issues]
    return {
        "status": status.value,
        "source": source,
        "part_name": (drawing.part.part_number or drawing.part.part_name) if drawing else None,
        "units": "mm",
        "coordinate_system": {"origin": "base_lower_left_bottom", "x": "length", "y": "width", "z": "height",
                              "plan_view": drawing.plan_view if drawing else None},
        "base": base,
        "features": [_feature(f) for f in drawing.features] if drawing else [],
        "tolerances": [t.model_dump() for t in drawing.tolerances] if drawing else [],
        "gdt": [x.model_dump() for x in drawing.gdt] if drawing else [],
        "measurements": ctx.conversions,
        "validation": {s: ctx.stage_status(s).value for s in STAGES},
        "warnings": [i for i in issues if i["status"] == "WARNING"],
        "errors": [i for i in issues if i["status"] != "WARNING"],
    }


def build_report(validated: dict[str, Any], ctx: Context) -> dict[str, Any]:
    by_stage: dict[str, list[dict]] = {s: [] for s in STAGES}
    for i in ctx.issues:
        by_stage[i.stage].append(i.model_dump())
    return {
        "status": validated["status"],
        "source": validated["source"],
        "summary": {
            "stages": validated["validation"],
            "counts": dict(Counter(i.status for i in ctx.issues)),
            "codes": dict(Counter(i.code for i in ctx.issues if i.status != "WARNING")),
            "features": len(validated["features"]),
            "positions_solved": sum(1 for f in validated["features"] for p in f.get("instances", [])
                                    if p["x"] is not None and p["y"] is not None),
            "positions_total": sum(len(f.get("instances", [])) for f in validated["features"]),
        },
        "cad_generation_allowed": validated["status"] == "PASS",
        "issues_by_stage": by_stage,
        "solver_trace": ctx.trace,
    }
