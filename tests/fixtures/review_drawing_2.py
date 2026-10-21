"""Reviewer decisions for input/drawing_2.png (clevis bracket), applied through the web API.

Run with the web app up:  python -m tests.fixtures.review_drawing_2
The drawing is internally inconsistent in several places; each decision states what is PRINTED, what
CONFLICTS, and what was chosen.
"""
from __future__ import annotations

import copy
import json
import urllib.request

API = "http://127.0.0.1:8000/api/parts/drawing_2"


def call(path: str = "", data=None, method: str = "GET"):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    return json.load(urllib.request.urlopen(req))


def q(value, raw, view, source="dimension"):
    return {"value": value, "unit": "mm", "status": "extracted",
            "evidence": {"raw_text": raw, "view": view, "source_type": source}}


def rel(type_, reference, value, orientation, instance=None, raw=None, view="FRONT VIEW", source="dimension", **kw):
    return {"type": type_, "reference": reference, "value": value, "unit": "mm", "orientation": orientation,
            "instance": instance, "target": "center",
            "evidence": {"raw_text": raw if raw is not None else str(value), "view": view, "source_type": source}, **kw}


def main() -> None:
    g = copy.deepcopy(call("/geometry")["geometry"])
    feats = {f["id"]: f for f in g["features"]}
    lug, rib = feats["LUG_001"], feats["GUSSET_001"]

    # R1 (CORRECTION, supported by the drawing): the FRONT VIEW shows the ears edge-on, spaced left-right, and the
    #    RIGHT VIEW shows their round-top profile, so the ear thickness runs along X (Stage 1 said y).
    lug["normal_axis"] = "x"
    # R2 (CONFLICT): FRONT and RIGHT views print 70 from the base top face to the ear top; SECTION A-A prints 70
    #    from the base bottom. Decision: follow the two principal views -> ear height 70 above the base.
    lug["dimensions"].pop("height_from_base", None)
    lug["dimensions"]["height"] = q(70, "70", "RIGHT VIEW")
    # R3 (CONFLICT): '55' to the hole centre with a Ø30 hole and an R20 full-round top would put the top of the
    #    hole exactly at the top of the ear (0 mm wall) and off-centre from the drawn round top. The drawing shows
    #    the hole concentric with the round top. Decision: hole centre 50 above the base (= 70 - R20), 5 mm wall.
    lug["dimensions"]["hole_height"] = q(50, "reviewer: concentric with R20 top (printed 55 gives 0 wall)",
                                         "RIGHT VIEW", "human_review")
    lug["dimensions"]["top_radius"] = q(20, "full round, width 40", "RIGHT VIEW")

    # R4 (PRINTED + convention): ears 40 apart centre-to-centre, 20 either side of the part centreline
    #    (FRONT VIEW '40' with '20 | 20' about the centreline); ear width 40 centred on the base
    #    (RIGHT VIEW chain 20 / 40 / 20).
    g["relationships"] = [r for r in g["relationships"] if r["feature_id"] != "LUG_001"]
    g["relationships"].append({"feature_id": "LUG_001", "relationships": [
        rel("offset_from_edge", "left_edge", 40, "horizontal", 1, raw="reviewer: centreline 60 - 20", source="human_review"),
        rel("center_to_center", "LUG_001 (instance 1)", 40, "horizontal", 2, raw="40"),
        # RIGHT VIEW (third-angle): its left edge is the part's front face (Y=0); '20' runs to the ear's face
        rel("offset_from_edge", "left_edge", 20, "horizontal", None, raw="20 (RIGHT VIEW chain 20/40/20)",
            view="RIGHT VIEW", target="edge", applies_to="all"),
    ]})

    # R5 (CONFLICT): RIB DETAIL prints rise 40 / run 40. With the ears' outer faces at X=30 and X=90, a 40 run
    #    leaves the 120 mm base by 10 mm, and a 40 rise reaches 5 mm into the Ø30 ear hole. The FRONT VIEW draws
    #    the ribs ending near the mounting-hole centrelines and well below the holes. Decision: run 20, rise 30.
    rib["dimensions"]["run"] = q(20, "reviewer: printed 40 leaves the base", "FRONT VIEW", "human_review")
    rib["dimensions"]["rise"] = q(30, "reviewer: printed 40 cuts into the Ø30 hole", "FRONT VIEW", "human_review")

    # R6: overall height = 15 base + 70 ears = 85 (FRONT/RIGHT); the SECTION A-A '70' is the conflicting reading.
    g["overall_dimensions"]["height"] = q(85, "reviewer: 15 + 70 (FRONT/RIGHT views)", "FRONT VIEW", "human_review")
    for k in ("length", "width"):  # repeated readings (TOP / SECTION / RIGHT) agree; keep them for the multi-view check
        for o in g["overall_dimensions"][k].get("occurrences", []):
            if o.get("rejected_value") is not None:
                o["value"] = o.pop("rejected_value")
                o.pop("rejection_reason", None)

    g["missing_information"] = []
    g["ambiguities"] = []
    note = ("R1 ear thickness along X (front view shows ears edge-on); R2 ear height 70 above base (front/right; "
            "section A-A conflicts); R3 hole centre 50 = concentric with R20 top (printed 55 gives 0 wall); "
            "R4 ears at X=40/80, Y centred (40 c-c, 20|20 about centreline; 20/40/20 right view); "
            "R5 rib run 20 / rise 30 (printed 40x40 leaves the base and cuts the hole); R6 overall height 85.")
    r = call("/geometry", {"geometry": g, "note": note, "revalidate": True}, "PUT")
    print("saved review:", len(r["changes"]), "changes")


if __name__ == "__main__":
    main()
