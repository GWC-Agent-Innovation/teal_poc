"""Normalize every Measurement to mm (lengths) or degrees (angles). Never converts unknown units."""
from __future__ import annotations

import logging

from utils import units

from .models import Context, Drawing, Measurement

log = logging.getLogger("stage2.units")


def normalize_measurement(m: Measurement, drawing_unit: str | None, ctx: Context) -> None:
    if not isinstance(m.original_value, float):
        return  # missing or non-numeric: already reported, nothing to convert
    fid = m.field.split(".")[0]
    if m.kind == "angle":
        unit = m.original_unit
        if unit is None or units.is_angle_unit(unit):
            m.unit_used, m.value = "deg", m.original_value
            return
        ctx.add("REVIEW_REQUIRED", "units", "UNKNOWN_UNIT",
                f"{m.field}: angle unit '{unit}' is not supported.", fid, unit=unit)
        return

    unit, inherited = m.original_unit, False
    if unit is None:
        unit, inherited = drawing_unit, True
    if unit is None:
        ctx.add("REVIEW_REQUIRED", "units", "UNIT_MISSING",
                f"{m.field} = {m.original_value:g} has no unit and the drawing declares none.", fid)
        return
    if not units.is_length_unit(unit):
        ctx.add("REVIEW_REQUIRED", "units", "UNKNOWN_UNIT",
                f"{m.field}: unit '{unit}' is not a supported length unit; not converted.", fid, unit=unit)
        return
    m.unit_used = unit
    m.value = units.to_mm(m.original_value, unit)
    ctx.conversions.append({"field": m.field, "original_value": m.original_value, "original_unit": m.original_unit,
                            "unit_used": unit, "inherited_from_drawing": inherited,
                            "normalized_value_mm": m.value, "evidence": m.evidence})
    if units.normalize_unit(unit) != "mm":
        log.info("Converted %s: %g %s -> %g mm", m.field, m.original_value, unit, m.value)

    for occ in m.occurrences:
        v, u = occ.get("value"), occ.get("unit") or unit
        if isinstance(v, (int, float)) and not isinstance(v, bool) and units.is_length_unit(u):
            occ["value_mm"] = units.to_mm(float(v), u)


def normalize(drawing: Drawing, ctx: Context) -> None:
    du = drawing.part.units
    if du is not None and not units.is_length_unit(du):
        ctx.add("REVIEW_REQUIRED", "units", "UNKNOWN_UNIT",
                f"drawing unit '{du}' is not supported; values without their own unit are not converted.", unit=du)
        du = None
    for m in drawing.all_measurements():
        normalize_measurement(m, du, ctx)
    log.info("Normalized %d measurement(s) to mm", len(ctx.conversions))
