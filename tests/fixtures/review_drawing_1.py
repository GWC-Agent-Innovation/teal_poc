"""Reviewer decisions for input/drawing_1.png, applied through the web API (same as the Review tab).

Run with the web app up:  python -m tests.fixtures.review_drawing_1
Each decision says whether it is PRINTED on the drawing or a REVIEWER ASSUMPTION.
"""
from __future__ import annotations

import copy
import json
import urllib.request

API = "http://127.0.0.1:8000/api/parts/drawing_1"


def call(path: str = "", data=None, method: str = "GET"):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    return json.load(urllib.request.urlopen(req))


def ev(raw, view="FRONT VIEW", source="human_review"):
    return {"raw_text": raw, "view": view, "source_type": source}


def rel(type_, reference, value, orientation, instance=None, target="center", raw=None, source="dimension", **kw):
    return {"type": type_, "reference": reference, "value": value, "unit": "mm", "orientation": orientation,
            "instance": instance, "target": target, "evidence": ev(raw if raw is not None else str(value), source=source), **kw}


def main() -> None:
    g = copy.deepcopy(call("/geometry")["geometry"])
    feats = {f["id"]: f for f in g["features"]}

    # D1 (REVIEWER ASSUMPTION): the two bottom holes are drawn identical to the '3 x Ø10 THRU' holes but no
    #    callout covers them. Decision: Ø10 THRU, same as the callout holes.
    feats["HOLE_002"]["dimensions"]["diameter"] = {
        "value": 10, "unit": "mm", "status": "extracted",
        "evidence": ev("reviewer: drawn identical to 3 x Ø10 THRU holes")}
    feats["HOLE_002"]["depth_type"] = "through"

    top = [r for r in g["relationships"] if r["feature_id"] == "HOLE_001"][0]
    top["relationships"] = [
        # PRINTED: chain 25 / 50 / 50 above the FRONT VIEW
        rel("offset_from_edge", "left_edge", 25, "horizontal", 1),
        rel("center_to_center", "HOLE_001 (instance 1)", 50, "horizontal", 2),
        rel("center_to_center", "HOLE_001 (instance 2)", 50, "horizontal", 3),
        # D2 (REVIEWER ASSUMPTION): the top row's vertical position is not dimensioned and the image is not to
        #    scale. 25 from the top (mirroring the bottom row) makes the middle hole tangent to the slot, which
        #    Stage 2 rejects (ZERO_WALL_THICKNESS). Decision: 15 from the top edge, reproducing the drawn layout
        #    (holes near the top edge, clear gap above the slot; 10 mm walls both sides). TYP = all three.
        rel("offset_from_edge", "top_edge", 15, "vertical", None, raw="reviewer: 15 TYP (not on drawing)",
            source="human_review", applies_to="all"),
    ]
    bottom = [r for r in g["relationships"] if r["feature_id"] == "HOLE_002"][0]
    bottom["relationships"] = [
        rel("offset_from_edge", "left_edge", 25, "horizontal", 1),     # PRINTED
        rel("offset_from_edge", "right_edge", 25, "horizontal", 2),    # PRINTED
        rel("offset_from_edge", "bottom_edge", 25, "vertical", 1),     # PRINTED (left hole)
        # D3 (REVIEWER ASSUMPTION): right bottom hole drawn on the same horizontal centreline as the left one.
        rel("aligned_with", "HOLE_002 (instance 1) horizontal centerline", None, "horizontal", 2,
            raw="reviewer: same centreline as bottom-left hole", source="human_review"),
    ]
    slot = [r for r in g["relationships"] if r["feature_id"] == "SLOT_001"][0]
    # PRINTED (note 3): "CENTER SLOT: 100 x 20 mm, R10 ENDS."
    slot["relationships"] = [rel("centered_on_plate", "base", None, None, 1,
                                 raw="3. CENTER SLOT: 100 x 20 mm, R10 ENDS.", source="note")]

    # All Stage 1 missing/ambiguous items are resolved above; projection does not affect this part's geometry.
    g["missing_information"] = []
    g["ambiguities"] = []

    note = ("D1 bottom holes Ø10 THRU (assumed same as callout holes); D2 top-row holes 25 from top edge "
            "(not dimensioned; 25 would be tangent to the slot); D3 bottom-right hole on bottom-left hole's centreline (assumed); "
            "slot centred per note 3; chain 25/50/50 references fixed; projection ambiguity irrelevant.")
    r = call("/geometry", {"geometry": g, "note": note, "revalidate": True}, "PUT")
    print("saved review:", len(r["changes"]), "changes")


if __name__ == "__main__":
    main()
