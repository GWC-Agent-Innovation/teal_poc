"""Map Stage 1 relationships onto canonical kinds + axes of the part coordinate system.

Frame: X = base length, Y = base width, Z = height; origin at the lower-left corner of the
base profile on its bottom face. The *plan view* (the view showing the base profile) maps
horizontal -> X and vertical -> Y. Elevation views map vertical -> Z.
Nothing is solved here; that is coordinate_solver's job.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Optional

from .models import POSITIONED_TYPES, Context, Drawing, FeatureRelationship
from .schema_validator import canonical_view, parse_measurement
from .unit_normalizer import normalize_measurement

log = logging.getLogger("stage2.relationships")

EDGE_TYPES = {"offset_from_edge", "from_edge", "distance_from_edge", "edge_offset", "edge_distance", "offset",
              "distance", "position", "location", "coordinate"}
DIRECT_EDGE_KINDS = {"from_left", "from_right", "from_top", "from_bottom"}
FEATURE_OFFSET_TYPES = {"center_to_center", "offset_from_feature", "distance_between", "pitch", "spacing",
                        "offset_from_hole", "offset_from_centerline", "center_distance"}
ALIGN_TYPES = {"aligned_with", "collinear", "on_centerline", "aligned"}
CENTER_TYPES = {"centered", "centered_on_plate", "centered_on_base", "centered_on_part", "centered_on",
                "centered_horizontal", "centered_horizontally", "centered_vertical", "centered_vertically"}
CONCENTRIC_TYPES = {"concentric", "coaxial", "concentric_with"}
INFO_TYPES = {"coincident", "parallel", "perpendicular"}
NON_POSITIONAL = {"feature_length", "size", "length", "width", "diameter", "height", "thickness", "depth"}


def choose_plan_view(drawing: Drawing, ctx: Context) -> Optional[str]:
    """Deterministic: the view carrying most positioned-feature sizes, else a TOP view."""
    votes: Counter[str] = Counter()
    plan_sizes = {"hole": ("diameter",), "slot": ("length", "width"), "pocket": ("length", "width"),
                  "cutout": ("length", "width"), "boss": ("diameter",)}  # sizes seen true-shape in plan
    for f in drawing.features:
        if f.type not in plan_sizes:
            continue
        for field in plan_sizes[f.type]:
            m = getattr(f, field, None)
            if m is not None and m.view and canonical_view(m.view) not in ("ISOMETRIC", "SECTION", "DETAIL"):
                votes[m.view] += 1
        v = (f.evidence or {}).get("view")
        if v and canonical_view(v) not in ("ISOMETRIC", "SECTION", "DETAIL"):
            votes[v] += 1
    if votes:
        view, n = votes.most_common(1)[0]
        basis = f"carries {n} positioned-feature callout(s)"
    else:
        view = next((v.name for v in drawing.views if v.canonical == "TOP"), None)
        basis = "named TOP"
    if view is None:
        ctx.add("REVIEW_REQUIRED", "relationships", "PLAN_VIEW_UNKNOWN",
                "cannot tell which view shows the base profile (X/Y plane).")
        return None
    ctx.add("WARNING", "relationships", "PLAN_VIEW_SELECTED",
            f"'{view}' used as the X/Y plan view ({basis}).", view=view)
    return view


def _frame(view: Optional[str], source_type: Optional[str], drawing: Drawing) -> str:
    """plan | elev_x (front/back) | elev_y (right/left) | unknown."""
    if view is None:
        return "plan"
    if drawing.plan_view and (view == drawing.plan_view or canonical_view(view) == canonical_view(drawing.plan_view)):
        return "plan"
    c = canonical_view(view)
    if c in ("FRONT", "BACK"):
        return "elev_x"
    if c in ("RIGHT", "LEFT"):
        return "elev_y"
    if c == "TOP" and drawing.plan_view is None:
        return "plan"
    return "unknown"


def _side(ref: str) -> Optional[str]:
    r = ref.lower()
    for side in ("left", "right", "top", "bottom"):
        if re.search(rf"(?<![a-z]){side}(?![a-z])", r):
            return side
    if re.search(r"(?<![a-z])(upper)(?![a-z])", r):
        return "top"
    if re.search(r"(?<![a-z])(lower)(?![a-z])", r):
        return "bottom"
    return None


def _axis_from_orientation(orientation: Optional[str], frame: str) -> Optional[str]:
    o = (orientation or "").lower()
    if o not in ("horizontal", "vertical"):
        return None
    if frame == "plan":
        return "x" if o == "horizontal" else "y"
    if frame == "elev_x":
        return "x" if o == "horizontal" else "z"
    if frame == "elev_y":
        return "y" if o == "horizontal" else "z"
    return None


def _target(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    t = raw.lower()
    if "arc" in t or ("end" in t and "cent" in t):
        return "arc_center"
    if "cent" in t or "axis" in t:
        return "center"
    if any(w in t for w in ("edge", "tangent", "side", "end", "face")):
        return "edge"
    return "unrecognized"


def _find_feature(ref: str, drawing: Drawing) -> Optional[str]:
    ids = sorted((f.id for f in drawing.features), key=len, reverse=True)
    up = ref.upper()
    return next((i for i in ids if i.upper() in up), None)


def _instance(v: Any) -> tuple[Optional[int], bool]:
    """(instance, ok). Non-integer instance text is ambiguous."""
    if v is None:
        return None, True
    if isinstance(v, int) and not isinstance(v, bool):
        return v, True
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip()), True
    return None, False


def _ref_instance(r: dict) -> Optional[int]:
    if isinstance(r.get("reference_instance"), int):
        return r["reference_instance"]
    m = re.search(r"instance[\s_#]*(\d+)|#(\d+)", str(r.get("reference") or ""), re.IGNORECASE)
    return int(m.group(1) or m.group(2)) if m else None


def _direction(r: dict) -> Optional[int]:
    d = str(r.get("direction") or "").lower()
    if d in ("+x", "+y", "+z", "right", "up", "positive", "+"):
        return 1
    if d in ("-x", "-y", "-z", "left", "down", "negative", "-"):
        return -1
    return None


def _centerline_axis(ref: str) -> Optional[str]:
    r = ref.lower()
    if "vertical" in r:
        return "x"   # a vertical centerline fixes X
    if "horizontal" in r:
        return "y"
    return None


def _map_one(r: dict, drawing: Drawing, ctx: Context) -> Optional[FeatureRelationship]:
    fid = str(r.get("feature_id") or "")
    t = str(r.get("type") or "").lower()
    ref = str(r.get("reference") or "")
    ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else None
    view = (ev or {}).get("view")
    frame = _frame(view, (ev or {}).get("source_type"), drawing)
    raw = {k: v for k, v in r.items() if k != "feature_id"}

    feat = drawing.feature(fid)
    if feat is None:
        ctx.add("REVIEW_REQUIRED", "relationships", "UNKNOWN_FEATURE_REFERENCE",
                f"relationship refers to unknown feature '{fid}'.", fid or None, relationship=raw)
        return None
    if feat.type == "gusset":
        ctx.add("WARNING", "relationships", "RELATIONSHIP_NOT_USED",
                f"{fid}: ribs are placed from the feature they are attached to; '{t}' is not used.", fid)
        return None
    if t in NON_POSITIONAL:
        ctx.add("WARNING", "relationships", "NON_POSITIONAL_RELATIONSHIP",
                f"{fid}: '{t}' describes a size, not a position; ignored by the solver.", fid, relationship=raw)
        return None
    if frame == "unknown":
        ctx.add("REVIEW_REQUIRED", "relationships", "VIEW_AXIS_UNKNOWN",
                f"{fid}: dimension from view '{view}' cannot be mapped to part axes.", fid, relationship=raw)
        return None
    if view is None and (ev or {}).get("source_type") != "table" and t not in INFO_TYPES:
        ctx.add("WARNING", "relationships", "VIEW_ASSUMED_PLAN",
                f"{fid}: relationship has no view; interpreted in the plan view.", fid)

    inst, ok = _instance(r.get("instance"))
    if not ok:
        ctx.add("REVIEW_REQUIRED", "relationships", "AMBIGUOUS_INSTANCE",
                f"{fid}: instance '{r.get('instance')}' is not a single instance number.", fid, relationship=raw)
        return None

    value = parse_measurement(r.get("value") if not isinstance(r.get("value"), dict) else r["value"],
                              f"{fid}.relationship.{t}", ctx, fid)
    if value is not None and value.original_unit is None and r.get("unit"):
        value.original_unit = r.get("unit")
    if value is not None and value.evidence is None:
        value.evidence = ev
    if value is not None:
        normalize_measurement(value, drawing.part.units, ctx)
    common: dict[str, Any] = dict(feature_id=fid, instance=inst, value=value, view=view, evidence=ev, raw=raw,
                                  target=_target(r.get("target")), direction=_direction(r))

    def need_value(kind: str) -> bool:
        if value is None or value.value is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "MISSING_RELATIONSHIP_VALUE",
                    f"{fid}: '{kind}' relationship has no usable value.", fid, relationship=raw)
            return False
        return True

    # --- edge offsets --------------------------------------------------------------------------
    ref_feature = _find_feature(ref, drawing)
    if t in DIRECT_EDGE_KINDS or (t in EDGE_TYPES and ref_feature is None) or \
            (t in FEATURE_OFFSET_TYPES and ref_feature is None and "edge" in ref.lower()):
        side = t.split("_", 1)[1] if t in DIRECT_EDGE_KINDS else _side(ref)
        if side is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "UNKNOWN_REFERENCE",
                    f"{fid}: cannot tell which edge '{ref}' is.", fid, relationship=raw)
            return None
        if not need_value("offset"):
            return None
        if frame == "plan":
            return FeatureRelationship(kind=f"from_{side}", axis="x" if side in ("left", "right") else "y", **common)
        if frame == "elev_x" and side in ("left", "right"):
            return FeatureRelationship(kind=f"from_{side}", axis="x", **common)
        if frame == "elev_y" and side in ("left", "right"):
            # third-angle right view: left of view = front of part (Y=0)
            right_view = canonical_view(view) == "RIGHT"
            kind = ("from_bottom" if side == "left" else "from_top") if right_view else \
                   ("from_top" if side == "left" else "from_bottom")
            ctx.add("WARNING", "relationships", "SIDE_VIEW_MAPPING",
                    f"{fid}: {side} edge in '{view}' mapped to {kind} (Y) assuming third-angle projection.", fid)
            return FeatureRelationship(kind=kind, axis="y", **common)  # type: ignore[arg-type]
        if side == "bottom":
            return FeatureRelationship(kind="from_base_bottom", axis="z", **common)
        ctx.add("REVIEW_REQUIRED", "relationships", "UNSUPPORTED_REFERENCE",
                f"{fid}: '{ref}' in elevation view '{view}' is not supported.", fid, relationship=raw)
        return None

    # --- feature-to-feature offsets -------------------------------------------------------------
    if t in FEATURE_OFFSET_TYPES or (t in EDGE_TYPES and ref_feature is not None):
        n = getattr(feat, "quantity", None) or 1
        same_group = ref_feature is None and ref.strip().lower() in ("", "none", "null", "same", "self") or \
            (ref_feature == fid and _ref_instance(r) is None and inst is None)
        if same_group and n > 1:  # spacing between copies of one feature, e.g. "80" between 4X holes
            axis = _axis_from_orientation(r.get("orientation"), frame)
            if axis is None or not need_value(t):
                if axis is None:
                    ctx.add("REVIEW_REQUIRED", "relationships", "AXIS_UNKNOWN",
                            f"{fid}: spacing orientation ({r.get('orientation')!r}) does not give an axis.", fid,
                            relationship=raw)
                return None
            return FeatureRelationship(kind="pattern_spacing", axis=axis, **common)  # type: ignore[arg-type]
        if ref_feature is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "UNKNOWN_REFERENCE",
                    f"{fid}: '{t}' reference '{ref}' is not a known feature.", fid, relationship=raw)
            return None
        axis = _axis_from_orientation(r.get("orientation"), frame)
        if axis is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "AXIS_UNKNOWN",
                    f"{fid}: orientation of '{t}' ({r.get('orientation')!r}) does not give an axis.", fid,
                    relationship=raw)
            return None
        if not need_value(t):
            return None
        return FeatureRelationship(kind="center_to_center", axis=axis, reference_feature=ref_feature,  # type: ignore[arg-type]
                                   reference_instance=_ref_instance(r), **common)

    # --- alignment / centering -----------------------------------------------------------------
    if t in ALIGN_TYPES or t in CENTER_TYPES or t == "symmetric":
        if t in ALIGN_TYPES and ref_feature is not None:
            axis = _centerline_axis(ref) or _axis_from_orientation(r.get("axis"), frame)
            if axis is None:
                ctx.add("REVIEW_REQUIRED", "relationships", "AXIS_UNKNOWN",
                        f"{fid}: aligned with {ref_feature} but along which axis is not stated.", fid, relationship=raw)
                return None
            return FeatureRelationship(kind="aligned_with", axis=axis, reference_feature=ref_feature,  # type: ignore[arg-type]
                                       reference_instance=_ref_instance(r), **common)
        if t in ("centered_horizontal", "centered_horizontally"):
            return FeatureRelationship(kind="centered_horizontal", axis="x", **common)
        if t in ("centered_vertical", "centered_vertically"):
            return FeatureRelationship(kind="centered_vertical", axis="y", **common)
        axis = _centerline_axis(ref) if frame == "plan" else None
        if axis is None and t in CENTER_TYPES:
            return FeatureRelationship(kind="centered_on_base", **common)
        if axis is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "AXIS_UNKNOWN",
                    f"{fid}: '{t}' reference '{ref}' does not identify an axis.", fid, relationship=raw)
            return None
        if t in ALIGN_TYPES:
            ctx.add("WARNING", "relationships", "CENTERLINE_AS_PART_CENTER",
                    f"{fid}: alignment with '{ref}' interpreted as the base's centre line.", fid)
        kind = "centered_horizontal" if axis == "x" else "centered_vertical"
        return FeatureRelationship(kind=kind, axis=axis, **common)  # type: ignore[arg-type]

    if t in CONCENTRIC_TYPES:
        if ref_feature is None:
            ctx.add("REVIEW_REQUIRED", "relationships", "UNKNOWN_REFERENCE",
                    f"{fid}: concentric reference '{ref}' is not a known feature.", fid, relationship=raw)
            return None
        return FeatureRelationship(kind="concentric", reference_feature=ref_feature,
                                   reference_instance=_ref_instance(r), **common)

    # --- patterns --------------------------------------------------------------------------------
    if t in ("linear_pattern", "equal_spacing"):
        axis = _axis_from_orientation(r.get("orientation"), frame)
        count = r.get("count") if isinstance(r.get("count"), int) else getattr(feat, "quantity", None)
        if axis is None or not count or not need_value(t):
            if axis is None or not count:
                ctx.add("REVIEW_REQUIRED", "relationships", "INCOMPLETE_PATTERN",
                        f"{fid}: linear pattern needs orientation, count and spacing.", fid, relationship=raw)
            return None
        return FeatureRelationship(kind="linear_pattern", axis=axis, count=count, **common)  # type: ignore[arg-type]
    if t == "circular_pattern":
        count = r.get("count") if isinstance(r.get("count"), int) else getattr(feat, "quantity", None)
        angle = parse_measurement(r.get("start_angle"), f"{fid}.pattern.start_angle", ctx, fid, "angle")
        if angle is not None:
            normalize_measurement(angle, drawing.part.units, ctx)
        if not count or angle is None or angle.value is None or not need_value(t):
            if not count or angle is None or angle.value is None:
                ctx.add("REVIEW_REQUIRED", "relationships", "INCOMPLETE_PATTERN",
                        f"{fid}: circular pattern needs count, pitch-circle diameter (value) and start_angle.",
                        fid, relationship=raw)
            return None
        return FeatureRelationship(kind="circular_pattern", count=count, angle=angle,
                                   reference_feature=ref_feature, **common)

    if t in INFO_TYPES:
        return FeatureRelationship(kind=t, reference_feature=ref_feature, **common)  # type: ignore[arg-type]

    ctx.add("REVIEW_REQUIRED", "relationships", "UNSUPPORTED_RELATIONSHIP",
            f"{fid}: relationship type '{t}' is not supported.", fid, relationship=raw)
    return None


def map_relationships(drawing: Drawing, ctx: Context) -> None:
    drawing.plan_view = choose_plan_view(drawing, ctx)
    for r in drawing.raw_relationships:
        rel = _map_one(r, drawing, ctx)
        if rel is not None:
            drawing.relationships.append(rel)
            log.info("Relationship %s: %s%s", rel.feature_id, rel.describe(),
                     f" [instance {rel.instance}]" if rel.instance else "")
