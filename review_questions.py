"""Turn Stage 2 issues into plain questions for a reviewer, and apply the answers to the geometry.

Every answer is written back into the Stage 1-shaped geometry with evidence source_type "human_review",
so Stage 2 re-validates it like any other value and the audit log shows who decided what.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

POSITION_CODES = {"INSUFFICIENT_CONSTRAINTS", "AMBIGUOUS_INSTANCE", "UNKNOWN_REFERENCE", "AXIS_UNKNOWN",
                  "DIRECTION_UNKNOWN", "AMBIGUOUS_TARGET", "OVERCONSTRAINED_CONFLICT", "MISSING_RELATIONSHIP_VALUE",
                  "UNRESOLVED_REFERENCE", "INSTANCE_OUT_OF_RANGE", "VIEW_AXIS_UNKNOWN", "UNSUPPORTED_REFERENCE",
                  "PATTERN_NOT_A_GRID"}
ENUMS = {
    ("slot", "orientation"): ["along_x", "along_y"],
    ("lug", "normal_axis"): ["x", "y"],
    ("lug", "top_type"): ["full_round", "flat", "radius"],
    ("gusset", "side"): ["outer", "inner", "plus", "minus"],
}
ENUM_CODES = {"SLOT_ORIENTATION_UNKNOWN": ("slot", "orientation"), "LUG_ORIENTATION_UNKNOWN": ("lug", "normal_axis"),
              "LUG_TOP_UNKNOWN": ("lug", "top_type"), "GUSSET_SIDE_UNKNOWN": ("gusset", "side")}
DEPTH_CODES = {"HOLE_DEPTH_UNKNOWN", "SLOT_DEPTH_UNKNOWN"}


def _feature(geometry: dict, fid: str) -> Optional[dict]:
    return next((f for f in geometry.get("features", []) if f.get("id") == fid), None)


def _vfeature(validated: dict, fid: str) -> Optional[dict]:
    return next((f for f in validated.get("features", []) if f.get("id") == fid), None)


def build_questions(validated: dict, geometry: dict) -> list[dict[str, Any]]:
    """One question per thing a person has to decide. Stable ids so answers can be matched."""
    qs: dict[str, dict] = {}
    base = validated.get("base") or {}

    def add(q: dict) -> None:
        qs.setdefault(q["id"], q)

    for e in validated.get("errors", []):
        code, fid, ev = e["code"], e.get("feature_id"), e.get("evidence") or {}
        vf = _vfeature(validated, fid) if fid else None
        if code in ("MISSING_REQUIRED_DIMENSION", "LUG_HOLE_INCOMPLETE") and fid:
            fields = ev.get("missing") or (["hole_diameter", "hole_height"] if code == "LUG_HOLE_INCOMPLETE" else [])
            for field in fields:
                target = "base" if fid == base.get("id") else "feature"
                add({"id": f"value:{fid}:{field}", "kind": "value", "feature_id": fid, "field": field,
                     "target": target, "unit": "mm", "question": f"{fid}: what is the {field.replace('_', ' ')}?",
                     "why": e["message"]})
        elif code in POSITION_CODES and fid and vf is not None and vf.get("instances") is not None:
            inst = vf.get("instances", [])
            add({"id": f"position:{fid}", "kind": "position", "feature_id": fid,
                 "question": f"Where is {fid} ({vf['type']}, {len(inst)} instance(s))? "
                             "Give each centre in mm from the base's left edge (x) and front/bottom edge (y).",
                 "why": e["message"], "base": [base.get("length"), base.get("width")],
                 "instances": [{"x": p.get("x"), "y": p.get("y")} for p in inst]})
        elif code in ENUM_CODES and fid:
            ftype, field = ENUM_CODES[code]
            add({"id": f"enum:{fid}:{field}", "kind": "choice", "feature_id": fid, "field": field,
                 "question": f"{fid}: {field.replace('_', ' ')}?", "options": ENUMS[(ftype, field)], "why": e["message"]})
        elif code in DEPTH_CODES and fid:
            add({"id": f"depth:{fid}", "kind": "depth", "feature_id": fid, "question": f"{fid}: through, or blind to what depth?",
                 "why": e["message"]})
        elif code == "EDGE_SELECTION_UNKNOWN" and fid:
            add({"id": f"edges:{fid}", "kind": "choice", "feature_id": fid, "field": "edges",
                 "question": f"{fid}: which edges does it apply to?",
                 "options": ["base_vertical_corners", "not modelled (remove from the CAD model)"], "why": e["message"]})
        elif code == "UNSUPPORTED_FEATURE" and fid:
            add({"id": f"unsupported:{fid}", "kind": "choice", "feature_id": fid, "field": "_remove",
                 "question": f"{fid} ({ev.get('stage1_type')}) cannot be modelled yet. Continue without it?",
                 "options": ["not modelled (remove from the CAD model)"], "why": e["message"]})
        elif code == "GUSSET_ATTACHMENT_UNKNOWN" and fid:
            hosts = [f["id"] for f in validated.get("features", []) if f["type"] in ("boss", "lug")]
            add({"id": f"enum:{fid}:attached_to", "kind": "choice", "feature_id": fid, "field": "attached_to",
                 "question": f"{fid}: which feature are the ribs attached to?", "options": hosts, "why": e["message"]})
            if any(f["type"] == "boss" for f in validated.get("features", [])):
                add({"id": f"dirs:{fid}", "kind": "choice", "feature_id": fid, "field": "directions",
                     "question": f"{fid}: if attached to a boss, in which directions do the ribs run from it?",
                     "options": ["+x and -x (both sides, along the length)", "+y and -y (both sides, along the width)",
                                 "+x", "-x", "+y", "-y"], "why": "needed to place ribs on a round boss"})
        elif code == "GUSSET_DIRECTIONS_UNKNOWN" and fid:
            add({"id": f"dirs:{fid}", "kind": "choice", "feature_id": fid, "field": "directions",
                 "question": f"{fid}: in which directions do the ribs run from the boss?",
                 "options": ["+x and -x (both sides, along the length)", "+y and -y (both sides, along the width)",
                             "+x", "-x", "+y", "-y"], "why": e["message"]})
        elif code == "GUSSET_POSITION_UNKNOWN" and fid:
            add({"id": f"enum:{fid}:centered_on_lug", "kind": "choice", "feature_id": fid, "field": "centered_on_lug",
                 "question": f"{fid}: is the rib centred on the face it is attached to?", "options": ["yes", "no"],
                 "why": e["message"]})
        elif code in ("STAGE1_AMBIGUITY", "STAGE1_MISSING_INFORMATION"):
            field = ev.get("field") or ""
            opts = list(ev.get("interpretations") or [])
            add({"id": f"stage1:{code}:{field}", "kind": "acknowledge", "field": field, "code": code,
                 "question": ("Stage 1 was unsure about " if code == "STAGE1_AMBIGUITY" else "Stage 1 reported missing: ")
                             + field, "why": e["message"], "options": opts})
        elif code == "MULTIVIEW_CONFLICT":
            vals = ev.get("values") or []
            add({"id": f"conflict:{ev.get('dimension')}", "kind": "conflict", "field": ev.get("dimension"),
                 "question": f"The drawing gives different values for {ev.get('dimension')}. Which is right?",
                 "options": [f"{v.get('value')} ({v.get('view')})" for v in vals], "why": e["message"]})
    # a Stage 1 note about something another question already asks is resolved by that answer, not asked twice
    actionable = [q for q in qs.values() if q["kind"] not in ("acknowledge",)]
    out = [q for q in qs.values() if q["kind"] != "acknowledge"
           or not any(_covers(a, q["field"]) for a in actionable)]
    order = {"value": 0, "position": 1, "choice": 2, "depth": 3, "conflict": 4, "acknowledge": 5}
    return sorted(out, key=lambda q: order.get(q["kind"], 9))


def _covers(q: dict, stage1_field: str) -> bool:
    """Does answering question q settle a Stage 1 missing/ambiguous entry about stage1_field?"""
    fid = q.get("feature_id")
    if not fid or not stage1_field.startswith(fid):
        return False
    rest = stage1_field[len(fid):].lower()
    if q["kind"] == "position":
        return any(w in rest for w in ("position", "location", "instance", "relationship")) or rest in ("", ".")
    field = str(q.get("field") or "").lower()
    return bool(field) and field in rest


def _human(value: Any, note: str) -> dict:
    return {"value": value, "unit": "mm", "status": "extracted",
            "evidence": {"raw_text": f"reviewer: {note}", "view": None, "source_type": "human_review"}}


def _rel(type_: str, ref: str, value: float, orientation: str, instance: int) -> dict:
    return {"type": type_, "reference": ref, "value": value, "unit": "mm", "orientation": orientation,
            "instance": instance, "target": "center",
            "evidence": {"raw_text": f"reviewer: {value:g}", "view": None, "source_type": "human_review"}}


def apply_answers(geometry: dict, answers: dict[str, Any]) -> tuple[dict, list[str]]:
    """Return (new_geometry, human-readable log). Unknown or empty answers are ignored."""
    g = copy.deepcopy(geometry)
    log: list[str] = []
    for qid, ans in answers.items():
        if ans in (None, "", [], {}):
            continue
        kind, _, rest = qid.partition(":")
        if kind == "value":
            fid, field = rest.split(":", 1)
            f = next((x for x in g.get("features", []) if x.get("id") == fid), None)
            if f is None:
                continue
            f.setdefault("dimensions", {})[field] = _human(float(ans), f"{field} = {float(ans):g}")
            log.append(f"{fid}.{field} = {float(ans):g} mm")
        elif kind == "position":
            fid = rest
            rows = [r for r in ans if r.get("x") not in (None, "") and r.get("y") not in (None, "")]
            if not rows:
                continue
            f = _feature(g, fid)
            if f is not None and len(rows) > (f.get("quantity") or 1):
                f["quantity"] = len(rows)
            g["relationships"] = [r for r in g.get("relationships", []) if r.get("feature_id") != fid]
            rels = []
            for i, r in enumerate(rows, start=1):
                rels += [_rel("offset_from_edge", "left_edge", float(r["x"]), "horizontal", i),
                         _rel("offset_from_edge", "bottom_edge", float(r["y"]), "vertical", i)]
            g["relationships"].append({"feature_id": fid, "relationships": rels})
            log.append(f"{fid} positions set: " + ", ".join(f"({float(r['x']):g}, {float(r['y']):g})" for r in rows))
        elif kind in ("enum", "edges", "unsupported"):
            fid, _, field = rest.partition(":")
            field = field or ("edges" if kind == "edges" else "_remove")
            f = _feature(g, fid)
            if f is None:
                continue
            if str(ans).startswith("not modelled"):
                g["features"] = [x for x in g["features"] if x.get("id") != fid]
                g["relationships"] = [r for r in g.get("relationships", []) if r.get("feature_id") != fid]
                log.append(f"{fid} removed from the CAD model (not modelled)")
            elif field == "centered_on_lug":
                f[field] = ans == "yes"
                log.append(f"{fid}.{field} = {f[field]}")
            else:
                f[field] = ans
                log.append(f"{fid}.{field} = {ans}")
        elif kind == "dirs":
            f = _feature(g, rest)
            if f is None:
                continue
            f["directions"] = [d for d in ("+x", "-x", "+y", "-y") if d in str(ans)]
            f["quantity"] = len(f["directions"])
            log.append(f"{rest}.directions = {f['directions']}")
        elif kind == "depth":
            f = _feature(g, rest)
            if f is None:
                continue
            if str(ans).lower() in ("through", "thru"):
                f["depth_type"] = "through"
                log.append(f"{rest} is through")
            else:
                f["depth_type"] = "blind"
                f.setdefault("dimensions", {})["depth"] = _human(float(ans), f"depth = {float(ans):g}")
                log.append(f"{rest} blind, depth {float(ans):g} mm")
        elif kind == "stage1":
            code, _, field = rest.partition(":")
            key = "ambiguities" if code == "STAGE1_AMBIGUITY" else "missing_information"
            before = len(g.get(key, []))
            g[key] = [x for x in g.get(key, []) if x.get("field") != field]
            if len(g[key]) < before:
                log.append(f"resolved {key[:-1] if key.endswith('s') else key} '{field}': {ans}")
        elif kind == "conflict":
            dim = rest
            try:
                value = float(str(ans).split(" ")[0])
            except ValueError:
                continue
            key = {"overall_length": "length", "overall_width": "width", "overall_height": "height"}.get(dim)
            if key:
                g.setdefault("overall_dimensions", {})[key] = _human(value, f"{dim} = {value:g} (conflict resolved)")
                base = next((x for x in g.get("features", []) if str(x.get("type", "")).startswith("base")), None)
                bkey = {"length": "length", "width": "width"}.get(key)
                if base is not None and bkey:
                    base.setdefault("dimensions", {})[bkey] = _human(value, f"{bkey} = {value:g} (conflict resolved)")
                log.append(f"{dim} = {value:g} mm (chosen from conflicting views)")
    _resolve_covered_stage1_notes(g, answers, log)
    return g, log


def _resolve_covered_stage1_notes(g: dict, answers: dict[str, Any], log: list[str]) -> None:
    answered = []
    for qid, ans in answers.items():
        if ans in (None, "", [], {}):
            continue
        kind, _, rest = qid.partition(":")
        if kind == "position":
            answered.append({"kind": "position", "feature_id": rest})
        elif kind in ("value", "enum"):
            fid, _, field = rest.partition(":")
            answered.append({"kind": kind, "feature_id": fid, "field": field})
        elif kind in ("dirs", "depth"):
            answered.append({"kind": kind, "feature_id": rest, "field": "directions" if kind == "dirs" else "depth"})
    for key in ("missing_information", "ambiguities"):
        keep = []
        for entry in g.get(key, []):
            field = str(entry.get("field", ""))
            if any(_covers(a, field) for a in answered):
                log.append(f"Stage 1 note '{field}' resolved by the answer")
            else:
                keep.append(entry)
        g[key] = keep
