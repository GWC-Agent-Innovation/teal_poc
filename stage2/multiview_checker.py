"""Compare the same dimension as read from different views. Never picks a winner."""
from __future__ import annotations

import logging
from typing import Optional

from utils.geometry import close

from .models import Boss, Context, Drawing, Measurement

log = logging.getLogger("stage2.multiview")


def _entries(m: Optional[Measurement], source: str) -> list[dict]:
    if m is None or m.value is None:
        return []
    out = [{"view": m.view, "value": m.value, "source": source,
            "raw_text": (m.evidence or {}).get("raw_text")}]
    for o in m.occurrences:
        if o.get("value_mm") is not None:
            out.append({"view": o.get("view"), "value": o["value_mm"], "source": f"{source} (occurrence)",
                        "raw_text": o.get("raw_text")})
    return out


def _compare(name: str, entries: list[dict], ctx: Context, feature_id: Optional[str] = None) -> None:
    if len(entries) < 2:
        return
    if all(close(e["value"], entries[0]["value"]) for e in entries):
        ctx.trace.append(f"multiview {name}: consistent across {sorted({str(e['view']) for e in entries})}")
        return
    ctx.add("REVIEW_REQUIRED", "multiview", "MULTIVIEW_CONFLICT",
            f"{name} differs between views: " + ", ".join(f"{e['view']}={e['value']:g}" for e in entries),
            feature_id, type="MULTIVIEW_CONFLICT", dimension=name,
            values=[{"view": e["view"], "value": e["value"], "source": e["source"]} for e in entries])


def _check_stacked_height(drawing: Drawing, ctx: Context) -> None:
    """With raised features, overall height must equal base thickness + the tallest raised feature."""
    b, oh = drawing.base, drawing.overall.get("height")
    if b is None or b.thickness is None or b.thickness.value is None or oh is None or oh.value is None:
        return
    heights = []
    for f in drawing.features:
        m = getattr(f, "height", None) if f.type in ("boss", "lug") else None
        if f.type in ("boss", "lug"):
            if m is None or m.value is None:
                return  # cannot compare yet
            heights.append((f.id, m))
    if not heights:
        return
    fid, top = max(heights, key=lambda x: x[1].value)
    stacked = b.thickness.value + top.value
    if abs(stacked - oh.value) > 1e-3:
        ctx.add("REVIEW_REQUIRED", "multiview", "MULTIVIEW_CONFLICT",
                f"overall height {oh.value:g} ({oh.view}) != base thickness {b.thickness.value:g} + {fid} height "
                f"{top.value:g} ({top.view}) = {stacked:g}", fid, type="MULTIVIEW_CONFLICT", dimension="overall_height",
                values=[{"view": oh.view, "value": oh.value, "source": "overall.height"},
                        {"view": top.view, "value": stacked, "source": f"base.thickness + {fid}.height"}])


def _check_chains(drawing: Drawing, ctx: Context) -> None:
    """A printed chain running from one edge of the part to the opposite edge must add up to that size."""
    from utils import units
    from .schema_validator import canonical_view
    b = drawing.base
    if b is None:
        return
    sizes = {"length": b.length, "width": b.width, "height": drawing.overall.get("height")}
    for c in drawing.chains:
        vals = c.get("values") or []
        ends = {str(c.get("from") or "").lower(), str(c.get("to") or "").lower()}
        if len(vals) < 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            continue
        sides = {s for e in ends for s in ("left", "right", "top", "bottom") if s in e}
        view = c.get("view")
        plan = drawing.plan_view is not None and canonical_view(view) == canonical_view(drawing.plan_view)
        c_view = canonical_view(view)
        if sides == {"left", "right"}:
            key = "width" if c_view in ("RIGHT", "LEFT") and not plan else "length"
        elif sides == {"top", "bottom"}:
            key = "width" if plan else "height"
        else:
            continue
        unit = c.get("unit") or drawing.part.units
        if not units.is_length_unit(unit):
            continue
        total = sum(units.to_mm(float(v), unit) for v in vals)
        m = sizes.get(key)
        if m is None or m.value is None:
            continue
        if abs(total - m.value) > 1e-3:
            ctx.add("REVIEW_REQUIRED", "multiview", "DIMENSION_CHAIN_MISMATCH",
                    f"chain {' + '.join(f'{v:g}' for v in vals)} = {total:g} in {view} spans the part's {key}, "
                    f"which is {m.value:g}.", b.id, chain=vals, view=view, expected=m.value, total=total)
        else:
            ctx.trace.append(f"chain {vals} in {view} adds up to {key} {m.value:g}")


def check(drawing: Drawing, ctx: Context) -> None:
    _check_chains(drawing, ctx)
    b = drawing.base
    raised = any(f.type in ("boss", "lug", "gusset") for f in drawing.features)  # overall height != plate
    pairs = [("overall_length", "length", "length"), ("overall_width", "width", "width")]
    if not raised:
        pairs.append(("overall_height", "thickness", "height"))
    for name, bfield, ofield in pairs:
        bm = getattr(b, bfield) if b else None
        om = drawing.overall.get(ofield)
        entries = _entries(bm, f"base.{bfield}")
        if om is not None and om is not bm:
            entries += _entries(om, f"overall.{ofield}")
        _compare(name, entries, ctx, b.id if b else None)

    _check_stacked_height(drawing, ctx)
    for f in drawing.features:
        for m in f.measurements():
            if m.occurrences:
                _compare(m.field, _entries(m, m.field), ctx, f.id)
    log.info("Multi-view check: %s", ctx.stage_status("multiview").value)
