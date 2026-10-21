"""Engineering sanity of sizes: presence, positivity, plausibility. Never modifies values."""
from __future__ import annotations

import logging
from typing import Optional

from .models import (BaseFeature, Boss, Chamfer, Context, Cutout, Drawing, Fillet, Gusset, Hole, Lug, Measurement,
                     Pocket, Slot)

log = logging.getLogger("stage2.dimensions")

REQUIRED: dict[str, tuple[str, ...]] = {
    "base": ("length", "width", "thickness"),
    "hole": ("diameter",),
    "slot": ("length", "width"),
    "pocket": ("length", "width", "depth"),
    "cutout": ("length", "width"),
    "boss": ("diameter", "height"),
    "lug": ("thickness", "width", "height"),
    "gusset": ("rise", "run", "thickness"),
    "fillet": ("radius",),
    "chamfer": ("distance", "angle"),
}


def val(m: Optional[Measurement]) -> Optional[float]:
    return m.value if m is not None else None


def resolve_base(drawing: Drawing, ctx: Context) -> None:
    """Fill base size from overall_dimensions where the base feature lacks it (evidence kept)."""
    raised = any(f.type in ("boss", "lug", "gusset") for f in drawing.features)  # overall height != plate
    if drawing.base is None:
        if drawing.overall.get("length") or drawing.overall.get("width"):
            drawing.base = BaseFeature(id="BASE", stage1_type="overall_dimensions")
            ctx.add("WARNING", "dimensions", "BASE_FROM_OVERALL",
                    "no base feature in Stage 1; base profile taken from overall dimensions.", "BASE")
        else:
            ctx.add("REVIEW_REQUIRED", "dimensions", "NO_BASE",
                    "no rectangular base feature or overall dimensions; POC cannot build this part.")
            return
    b = drawing.base
    mapping = {"length": "length", "width": "width", "thickness": "height"}
    for field, ov in mapping.items():
        if getattr(b, field) is not None and val(getattr(b, field)) is not None:
            continue
        if field == "thickness" and raised:
            continue  # overall height includes bosses; not the plate thickness
        om = drawing.overall.get(ov)
        if om is not None and om.value is not None:
            setattr(b, field, om)
            ctx.trace.append(f"base.{field} = {om.value:g} mm taken from overall_dimensions.{ov}")


def resolve_slot_lengths(drawing: Drawing, ctx: Context) -> None:
    """Overall slot length from a centre-to-centre dimension (deterministic arithmetic, logged)."""
    for f in drawing.features:
        if isinstance(f, Slot) and (f.length is None or f.length.value is None)                 and val(f.center_distance) is not None and val(f.width) is not None:
            total = f.center_distance.value + f.width.value  # type: ignore[union-attr,operator]
            f.length = Measurement(field=f"{f.id}.length", value=total, original_value=total, unit_used="mm",
                                   evidence={"derived_from": [f.center_distance.field, f.width.field]})
            ctx.trace.append(f"{f.id}.length = {total:g} mm = centre distance + width")


def _check_required(fid: str, ftype: str, obj, ctx: Context) -> None:
    for field in REQUIRED.get(ftype, ()):
        m: Optional[Measurement] = getattr(obj, field, None)
        if m is None or m.value is None:
            why = f"Stage 1 status '{m.stage1_status}'" if m is not None and m.stage1_status else "not provided"
            ctx.add("REVIEW_REQUIRED", "dimensions", "MISSING_REQUIRED_DIMENSION",
                    f"{ftype} {fid}: required dimension '{field}' is missing ({why}).", fid, missing=[field])


def _check_positive(obj, ctx: Context) -> None:
    for m in obj.measurements():
        if m.value is None or m.kind == "angle":
            continue
        if m.value <= 0:
            ctx.add("FAIL", "dimensions", "NON_POSITIVE_DIMENSION",
                    f"{m.field} = {m.value:g} mm must be > 0.", m.field.split(".")[0], value=m.value)


def _check_quantity(f, ctx: Context) -> None:
    if f.quantity is not None and f.quantity < 1:
        ctx.add("FAIL", "dimensions", "INVALID_QUANTITY", f"{f.id}: quantity {f.quantity} must be >= 1.", f.id)


def _plausibility(drawing: Drawing, ctx: Context) -> None:
    b = drawing.base
    L, W, T = (val(b.length), val(b.width), val(b.thickness)) if b else (None, None, None)
    smallest = min(x for x in (L, W) if x is not None) if (L or W) else None
    for f in drawing.features:
        if isinstance(f, Hole):
            d = val(f.diameter)
            if d is not None and smallest is not None and d >= smallest:
                ctx.add("FAIL", "dimensions", "HOLE_LARGER_THAN_BASE",
                        f"{f.id}: hole Ø{d:g} is not smaller than the base ({smallest:g} mm).", f.id,
                        diameter=d, base_min=smallest)
            if f.through is None and f.depth is None:
                ctx.add("REVIEW_REQUIRED", "dimensions", "HOLE_DEPTH_UNKNOWN",
                        f"{f.id}: neither THRU nor a depth is given.", f.id, missing=["depth"])
            if f.through is False and val(f.depth) is None:
                ctx.add("REVIEW_REQUIRED", "dimensions", "MISSING_REQUIRED_DIMENSION",
                        f"{f.id}: blind hole without a depth.", f.id, missing=["depth"])
            if f.thread is not None:
                ctx.add("WARNING", "dimensions", "THREAD_NOT_MODELED",
                        f"{f.id}: thread '{f.thread.designation}' is not modelled (P2); hole built at printed Ø.", f.id)
        elif isinstance(f, Slot):
            ln, w, er = val(f.length), val(f.width), val(f.end_radius)
            if ln is not None and w is not None and w >= ln:
                ctx.add("FAIL", "dimensions", "IMPOSSIBLE_SLOT",
                        f"{f.id}: slot width {w:g} must be smaller than its length {ln:g}.", f.id)
            if er is not None and w is not None and abs(2 * er - w) > 1e-3:
                ctx.add("REVIEW_REQUIRED", "dimensions", "SLOT_RADIUS_MISMATCH",
                        f"{f.id}: end radius R{er:g} does not equal half the width {w:g}.", f.id)
            if f.through is None and f.depth is None:
                ctx.add("REVIEW_REQUIRED", "dimensions", "SLOT_DEPTH_UNKNOWN",
                        f"{f.id}: neither THRU nor a depth is given.", f.id, missing=["depth"])
            if f.orientation is None:
                ctx.add("REVIEW_REQUIRED", "dimensions", "SLOT_ORIENTATION_UNKNOWN",
                        f"{f.id}: slot orientation (along X or Y) is not stated by Stage 1.", f.id,
                        missing=["orientation"])
        elif isinstance(f, (Pocket,)):
            dp = val(f.depth)
            if dp is not None and T is not None and dp >= T:
                ctx.add("FAIL", "dimensions", "IMPOSSIBLE_POCKET_DEPTH",
                        f"{f.id}: pocket depth {dp:g} is not less than base thickness {T:g}.", f.id)
        elif isinstance(f, Boss):
            d = val(f.diameter)
            if d is not None and smallest is not None and d > smallest:
                ctx.add("FAIL", "dimensions", "BOSS_LARGER_THAN_BASE",
                        f"{f.id}: boss Ø{d:g} exceeds the base ({smallest:g} mm).", f.id)
        elif isinstance(f, Lug):
            _check_lug(f, ctx)
        elif isinstance(f, Gusset):
            _check_gusset(f, drawing, ctx)
        elif isinstance(f, Fillet):
            r = val(f.radius)
            if f.edges is None:
                ctx.add("REVIEW_REQUIRED", "dimensions", "EDGE_SELECTION_UNKNOWN",
                        f"{f.id}: which edges the fillet applies to is not stated.", f.id)
            elif r is not None and smallest is not None and r > smallest / 2:
                ctx.add("FAIL", "dimensions", "FILLET_TOO_LARGE",
                        f"{f.id}: R{r:g} exceeds half the smallest base side ({smallest:g}).", f.id)
        elif isinstance(f, Chamfer):
            d, a = val(f.distance), val(f.angle)
            if f.edges is None:
                ctx.add("REVIEW_REQUIRED", "dimensions", "EDGE_SELECTION_UNKNOWN",
                        f"{f.id}: which edges the chamfer applies to is not stated.", f.id)
            elif d is not None and smallest is not None and d > smallest / 2:
                ctx.add("FAIL", "dimensions", "CHAMFER_TOO_LARGE",
                        f"{f.id}: chamfer {d:g} exceeds half the smallest base side ({smallest:g}).", f.id)
            if a is not None and not 0 < a < 90:
                ctx.add("FAIL", "dimensions", "INVALID_CHAMFER_ANGLE", f"{f.id}: angle {a:g}° must be in (0, 90).", f.id)


def lug_top_radius(f: Lug) -> Optional[float]:
    """Top radius: explicit, or half the width for a full-round top (drafting convention for a drawn semicircle)."""
    if f.top_type == "flat":
        return None
    if val(f.top_radius) is not None:
        return f.top_radius.value  # type: ignore[union-attr]
    if f.top_type == "full_round" and val(f.width) is not None:
        return f.width.value / 2  # type: ignore[union-attr,operator]
    return None


def _check_lug(f: Lug, ctx: Context) -> None:
    if f.normal_axis is None:
        ctx.add("REVIEW_REQUIRED", "dimensions", "LUG_ORIENTATION_UNKNOWN",
                f"{f.id}: which way the lug faces (normal_axis x or y) is not stated.", f.id, missing=["normal_axis"])
    if f.top_type is None and val(f.top_radius) is None:
        ctx.add("REVIEW_REQUIRED", "dimensions", "LUG_TOP_UNKNOWN",
                f"{f.id}: lug top shape (full_round / flat / radius) is not stated.", f.id, missing=["top_type"])
    w, h, r = val(f.width), val(f.height), lug_top_radius(f)
    tr = val(f.top_radius)
    if f.top_type == "full_round" and tr is not None and w is not None and abs(2 * tr - w) > 1e-3:
        ctx.add("REVIEW_REQUIRED", "dimensions", "LUG_RADIUS_MISMATCH",
                f"{f.id}: full-round top R{tr:g} is not half the width {w:g}.", f.id)
    if r is not None and w is not None and r > w / 2 + 1e-6:
        ctx.add("FAIL", "dimensions", "LUG_RADIUS_TOO_LARGE", f"{f.id}: top radius R{r:g} exceeds half the width.", f.id)
    if r is not None and h is not None and r > h:
        ctx.add("FAIL", "dimensions", "LUG_RADIUS_TOO_LARGE", f"{f.id}: top radius R{r:g} exceeds the lug height.", f.id)
    if (val(f.hole_diameter) is None) != (val(f.hole_height) is None):
        ctx.add("REVIEW_REQUIRED", "dimensions", "LUG_HOLE_INCOMPLETE",
                f"{f.id}: lug hole needs both a diameter and a centre height.", f.id)


def _check_gusset(f: Gusset, drawing: Drawing, ctx: Context) -> None:
    lug = drawing.feature(f.attached_to or "")
    if isinstance(lug, Boss):  # radial ribs around a boss
        if not f.directions:
            ctx.add("REVIEW_REQUIRED", "dimensions", "GUSSET_DIRECTIONS_UNKNOWN",
                    f"{f.id}: which directions the ribs run from {lug.id} (+x/-x/+y/-y) is not stated.", f.id)
        elif f.quantity and len(f.directions) != f.quantity:
            ctx.add("REVIEW_REQUIRED", "dimensions", "GUSSET_DIRECTIONS_UNKNOWN",
                    f"{f.id}: {f.quantity} ribs but {len(f.directions)} direction(s) given.", f.id)
        rise, bh = val(f.rise), val(lug.height)
        if rise is not None and bh is not None and rise > bh:
            ctx.add("FAIL", "dimensions", "GUSSET_TALLER_THAN_BOSS", f"{f.id}: rise {rise:g} exceeds boss height {bh:g}.", f.id)
        th, bd = val(f.thickness), val(lug.diameter)
        if th is not None and bd is not None and th >= bd:
            ctx.add("FAIL", "dimensions", "GUSSET_WIDER_THAN_BOSS", f"{f.id}: rib thicker than the boss diameter.", f.id)
        return
    if not isinstance(lug, Lug):
        ctx.add("REVIEW_REQUIRED", "dimensions", "GUSSET_ATTACHMENT_UNKNOWN",
                f"{f.id}: gusset must be attached_to a lug or boss feature (got {f.attached_to!r}).", f.id)
        return
    if f.side is None:
        ctx.add("REVIEW_REQUIRED", "dimensions", "GUSSET_SIDE_UNKNOWN",
                f"{f.id}: which face of {lug.id} the gusset is on (outer/inner/plus/minus) is not stated.", f.id)
    if f.centered_on_lug is not True:
        ctx.add("REVIEW_REQUIRED", "dimensions", "GUSSET_POSITION_UNKNOWN",
                f"{f.id}: gusset position across the lug face is not stated "
                "(set centered_on_lug if the drawing shows it).", f.id)
    rise, lh = val(f.rise), val(lug.height)
    if rise is not None and lh is not None and rise > lh:
        ctx.add("FAIL", "dimensions", "GUSSET_TALLER_THAN_LUG", f"{f.id}: rise {rise:g} exceeds lug height {lh:g}.", f.id)
    th, lw = val(f.thickness), val(lug.width)
    if th is not None and lw is not None and th > lw:
        ctx.add("FAIL", "dimensions", "GUSSET_WIDER_THAN_LUG", f"{f.id}: gusset thicker than the lug is wide.", f.id)


def validate(drawing: Drawing, ctx: Context) -> None:
    resolve_base(drawing, ctx)
    resolve_slot_lengths(drawing, ctx)
    if drawing.base is not None:
        _check_required(drawing.base.id, "base", drawing.base, ctx)
        _check_positive(drawing.base, ctx)
    for f in drawing.features:
        _check_quantity(f, ctx)
        _check_positive(f, ctx)
        if f.type != "unsupported":
            _check_required(f.id, f.type, f, ctx)
    _plausibility(drawing, ctx)
    log.info("Dimension validation: %s", ctx.stage_status("dimensions").value)
