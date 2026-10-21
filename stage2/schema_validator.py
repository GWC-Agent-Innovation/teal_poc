"""Parse Stage 1 geometry.json into typed Stage 2 entities.

Never repairs engineering values: a string like "approximately 10" stays
unresolved and is reported, it is not turned into 10.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .models import (
    BaseFeature, Boss, Chamfer, Context, Cutout, Drawing, Fillet, GDnT, Gusset, Hole, Lug, Measurement,
    Part, Pocket, Slot, Thread, Tolerance, UnsupportedFeature, View,
)

log = logging.getLogger("stage2.schema")

TYPE_ALIASES: dict[str, str] = {
    **dict.fromkeys(["base", "base_plate", "plate", "base_block", "block", "rectangular_plate",
                     "base_body", "rectangular_prism"], "base"),
    **dict.fromkeys(["hole", "through_hole", "blind_hole", "drilled_hole", "tapped_hole",
                     "threaded_hole", "bore", "mounting_hole"], "hole"),
    **dict.fromkeys(["slot", "straight_slot", "obround_slot", "through_slot"], "slot"),
    **dict.fromkeys(["pocket", "rectangular_pocket", "blind_pocket"], "pocket"),
    **dict.fromkeys(["cutout", "cut_out", "rectangular_cutout", "window"], "cutout"),
    **dict.fromkeys(["boss", "cylindrical_boss", "round_boss", "cylinder"], "boss"),
    **dict.fromkeys(["lug", "ear", "clevis_ear", "upright", "tab", "vertical_plate", "clevis_lug", "bracket_ear"], "lug"),
    **dict.fromkeys(["gusset", "rib", "web", "stiffener", "support_rib", "triangular_rib"], "gusset"),
    **dict.fromkeys(["fillet", "corner_fillet", "corner_radius", "radius", "edge_fillet", "round"], "fillet"),
    **dict.fromkeys(["chamfer", "corner_chamfer", "edge_chamfer", "bevel"], "chamfer"),
}

# canonical field -> accepted Stage 1 dimension names (first match wins)
DIM_ALIASES: dict[str, dict[str, list[str]]] = {
    "base": {"length": ["length", "overall_length"], "width": ["width", "overall_width", "depth_y"],
             "thickness": ["thickness", "height", "plate_thickness"]},
    "hole": {"diameter": ["diameter", "dia", "hole_diameter", "size"], "depth": ["depth", "hole_depth"]},
    "slot": {"length": ["length", "overall_length", "slot_length"], "width": ["width", "slot_width"],
             "end_radius": ["end_radius", "radius", "end_radii"], "depth": ["depth"],
             "center_distance": ["center_distance", "centre_distance", "center_to_center"]},
    "pocket": {"length": ["length"], "width": ["width"], "depth": ["depth"],
               "corner_radius": ["corner_radius", "radius"]},
    "cutout": {"length": ["length"], "width": ["width"], "corner_radius": ["corner_radius", "radius"]},
    "boss": {"diameter": ["diameter", "outer_diameter", "dia"], "height": ["height", "boss_height"]},
    "lug": {"thickness": ["thickness", "ear_thickness", "lug_thickness"], "width": ["width", "lug_width"],
            "height": ["height", "height_above_base", "lug_height", "overall_height_above_base"],
            "top_radius": ["top_radius", "outer_radius", "radius", "end_radius"],
            "hole_diameter": ["hole_diameter", "bore_diameter", "pin_hole_diameter"],
            "hole_height": ["hole_height", "hole_center_height", "height_to_hole_center"]},
    "gusset": {"rise": ["rise", "height", "vertical_leg"], "run": ["run", "length", "base_length", "horizontal_leg"],
               "thickness": ["thickness", "rib_thickness"]},
    "fillet": {"radius": ["radius", "fillet_radius", "r"]},
    "chamfer": {"distance": ["distance", "length", "size", "width", "chamfer_distance", "leg"],
                "angle": ["angle", "chamfer_angle"]},
}
ANGLE_FIELDS = {"angle"}
CLASSES = {"hole": Hole, "slot": Slot, "pocket": Pocket, "cutout": Cutout, "boss": Boss, "lug": Lug,
           "gusset": Gusset, "fillet": Fillet, "chamfer": Chamfer}

_VIEW_PATTERNS = [("SECTION", r"SECTION"), ("DETAIL", r"DETAIL"), ("ISOMETRIC", r"ISO"),
                  ("TOP", r"\bTOP\b|PLAN"), ("FRONT", r"FRONT"), ("RIGHT", r"RIGHT"), ("LEFT", r"LEFT"),
                  ("BOTTOM", r"BOTTOM"), ("BACK", r"BACK|REAR")]


def canonical_view(name: Optional[str]) -> str:
    if not name:
        return "UNKNOWN"
    up = str(name).upper()
    for canon, pat in _VIEW_PATTERNS:
        if re.search(pat, up):
            return canon
    return "OTHER"


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_measurement(raw: Any, field: str, ctx: Context, feature_id: Optional[str] = None,
                      kind: str = "length") -> Optional[Measurement]:
    """Raw Stage 1 quantity -> Measurement (value NOT yet normalized)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raw = {"value": raw}
    v = raw.get("value")
    m = Measurement(field=field, original_value=v, original_unit=raw.get("unit"), kind=kind,  # type: ignore[arg-type]
                    stage1_status=raw.get("status"), tolerance=raw.get("tolerance"),
                    evidence=raw.get("evidence") if isinstance(raw.get("evidence"), dict) else None,
                    occurrences=[o for o in raw.get("occurrences") or [] if isinstance(o, dict)])
    if v is None:
        return m
    if not _is_number(v):
        ctx.add("REVIEW_REQUIRED", "schema", "NON_NUMERIC_VALUE",
                f"{field} has a non-numeric value {v!r}; it is not interpreted.", feature_id, raw_value=v)
        return m
    m.original_value = float(v)
    return m


def parse_quantity(raw: Any, fid: str, ctx: Context) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not _is_number(raw):
        ctx.add("REVIEW_REQUIRED", "schema", "INVALID_QUANTITY",
                f"quantity {raw!r} is not a number; it is not interpreted.", fid, raw_value=raw)
        return None
    if float(raw) != int(raw):
        ctx.add("FAIL", "schema", "INVALID_QUANTITY", f"quantity {raw} is not a whole number.", fid, raw_value=raw)
        return None
    return int(raw)


def _text_of(f: dict) -> str:
    ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    return " ".join(str(x) for x in (f.get("type"), f.get("id"), ev.get("raw_text"), f.get("notes"),
                                     f.get("edges"), f.get("applies_to")) if x)


def _edges(f: dict) -> Optional[str]:
    if f.get("edges") in ("base_vertical_corners",):
        return f["edges"]
    return "base_vertical_corners" if re.search(r"corner", _text_of(f), re.IGNORECASE) else None


def _depth_flags(f: dict) -> Optional[bool]:
    dt = str(f.get("depth_type") or "").lower()
    if dt in ("through", "thru", "through_all"):
        return True
    if dt in ("blind",):
        return False
    return None


def _orientation(f: dict) -> Optional[str]:
    o = str(f.get("orientation") or "").lower()
    if o in ("along_x", "horizontal", "x"):
        return "along_x"
    if o in ("along_y", "vertical", "y"):
        return "along_y"
    return None


def _parse_feature(f: dict, ctx: Context, part_units: Optional[str]) -> tuple[Optional[str], Any]:
    fid = str(f.get("id") or "")
    stype = str(f.get("type") or "")
    if not fid or not stype:
        ctx.add("FAIL", "schema", "MALFORMED_FEATURE", "feature without id or type.", fid or None, raw=f)
        return None, None
    canon = TYPE_ALIASES.get(stype.lower())
    dims = f.get("dimensions") if isinstance(f.get("dimensions"), dict) else {}
    common = dict(id=fid, stage1_type=stype, quantity=parse_quantity(f.get("quantity"), fid, ctx),
                  evidence=f.get("evidence") if isinstance(f.get("evidence"), dict) else None)
    if canon is None:
        ctx.add("REVIEW_REQUIRED", "schema", "UNSUPPORTED_FEATURE",
                f"feature type '{stype}' is not supported by the POC CAD pipeline.", fid, stage1_type=stype)
        return "unsupported", UnsupportedFeature(**common, raw=f)

    fields: dict[str, Any] = {}
    used: set[str] = set()
    for canon_field, aliases in DIM_ALIASES[canon].items():
        for a in aliases:
            if a in dims and a not in used:
                used.add(a)
                fields[canon_field] = parse_measurement(
                    dims[a], f"{fid}.{canon_field}", ctx, fid, "angle" if canon_field in ANGLE_FIELDS else "length")
                break
    for extra in set(dims) - used:
        ctx.add("WARNING", "schema", "UNMAPPED_DIMENSION",
                f"dimension '{extra}' of {stype} is not used by Stage 2.", fid, dimension=extra)

    if canon == "base":
        return "base", BaseFeature(**common, **fields)
    if canon in ("hole", "slot"):
        fields["through"] = _depth_flags(f)
    if canon == "slot":
        fields["orientation"] = _orientation(f)
    if canon in ("fillet", "chamfer"):
        fields["edges"] = _edges(f)
    if canon == "lug":
        na = str(f.get("normal_axis") or "").lower()
        fields["normal_axis"] = na if na in ("x", "y") else None
        tt = str(f.get("top_type") or "").lower()
        fields["top_type"] = tt if tt in ("full_round", "flat", "radius") else None
    if canon == "gusset":
        fields["attached_to"] = f.get("attached_to")
        sd = str(f.get("side") or "").lower()
        fields["side"] = sd if sd in ("outer", "inner", "plus", "minus") else None
        fields["centered_on_lug"] = f.get("centered_on_lug") if isinstance(f.get("centered_on_lug"), bool) else None
        dirs = f.get("directions") or []
        dirs = [dirs] if isinstance(dirs, str) else dirs
        fields["directions"] = [d for d in (str(x).lower().replace(" ", "") for x in dirs) if d in ("+x", "-x", "+y", "-y")]
    if canon == "hole" and isinstance(f.get("thread"), dict) and f["thread"]:
        t = f["thread"]
        fields["thread"] = Thread(designation=t.get("designation"),
                                  depth=parse_measurement(t.get("depth"), f"{fid}.thread.depth", ctx, fid))
    return canon, CLASSES[canon](**common, **fields)


def parse(raw: Any, ctx: Context) -> Optional[Drawing]:
    """Return a Drawing, or None if the input is structurally unusable (FAIL issue added)."""
    if not isinstance(raw, dict):
        ctx.add("FAIL", "schema", "MALFORMED_INPUT", "geometry.json must be a JSON object.")
        return None
    if not isinstance(raw.get("features", []), list) or not isinstance(raw.get("relationships", []), list):
        ctx.add("FAIL", "schema", "MALFORMED_INPUT", "'features' and 'relationships' must be lists.")
        return None

    p = raw.get("part") if isinstance(raw.get("part"), dict) else {}
    part = Part(part_number=p.get("part_number"), part_name=p.get("part_name"), units=p.get("units"),
                material=p.get("material"))
    views = [View(name=str(v.get("name") if isinstance(v, dict) else v),
                  canonical=canonical_view(v.get("name") if isinstance(v, dict) else v))
             for v in raw.get("views") or []]
    od = raw.get("overall_dimensions") if isinstance(raw.get("overall_dimensions"), dict) else {}
    overall = {k: parse_measurement(od.get(k), f"overall.{k}", ctx) for k in ("length", "width", "height")}

    drawing = Drawing(part=part, views=views, overall=overall)
    seen: set[str] = set()
    for f in raw.get("features") or []:
        if not isinstance(f, dict):
            ctx.add("FAIL", "schema", "MALFORMED_FEATURE", f"feature entry is not an object: {f!r}")
            continue
        canon, feat = _parse_feature(f, ctx, part.units)
        if feat is None:
            continue
        if feat.id in seen:
            ctx.add("FAIL", "schema", "DUPLICATE_FEATURE_ID", f"feature id {feat.id} is used twice.", feat.id)
            continue
        seen.add(feat.id)
        if canon == "base":
            if drawing.base is not None:
                ctx.add("REVIEW_REQUIRED", "schema", "MULTIPLE_BASES",
                        f"more than one base feature ({drawing.base.id}, {feat.id}); only the first is used.", feat.id)
            else:
                drawing.base = feat
        else:
            drawing.features.append(feat)

    for group in raw.get("relationships") or []:
        if not isinstance(group, dict) or not isinstance(group.get("relationships", []), list):
            ctx.add("FAIL", "schema", "MALFORMED_RELATIONSHIP", f"relationship group is malformed: {group!r}")
            continue
        fid = group.get("feature_id")
        for r in group.get("relationships") or []:
            if not isinstance(r, dict):
                ctx.add("FAIL", "schema", "MALFORMED_RELATIONSHIP", f"relationship is not an object: {r!r}", fid)
                continue
            drawing.raw_relationships.append({**r, "feature_id": fid})

    for t in raw.get("tolerances") or []:
        if isinstance(t, dict):
            tol = Tolerance(applies_to=t.get("applies_to"), raw_text=t.get("raw_text"), kind=t.get("kind"))
            (drawing.gdt.append(GDnT(raw_text=tol.raw_text or "", feature_id=tol.applies_to))
             if tol.kind == "geometric" else drawing.tolerances.append(tol))
    drawing.notes = [str(n.get("text") if isinstance(n, dict) else n) for n in raw.get("notes") or []]
    drawing.chains = [c for c in raw.get("dimension_chains") or [] if isinstance(c, dict)]

    for m in raw.get("missing_information") or []:
        if isinstance(m, dict) and m.get("required_for_cad", True):
            ctx.add("REVIEW_REQUIRED", "schema", "STAGE1_MISSING_INFORMATION",
                    f"Stage 1 reported missing: {m.get('field')} - {m.get('reason')}",
                    str(m.get("field", "")).split(".")[0] or None, field=m.get("field"))
    for a in raw.get("ambiguities") or []:
        if isinstance(a, dict):
            ctx.add("REVIEW_REQUIRED", "schema", "STAGE1_AMBIGUITY",
                    f"Stage 1 reported ambiguity: {a.get('field')}", str(a.get("field", "")).split(".")[0] or None,
                    field=a.get("field"), interpretations=a.get("possible_interpretations"))

    log.info("Parsed %d feature(s) (+base: %s), %d relationship(s), %d view(s)",
             len(drawing.features), drawing.base.id if drawing.base else None,
             len(drawing.raw_relationships), len(views))
    return drawing
