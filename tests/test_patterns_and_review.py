"""Grid inference from group dimensions, dimension-chain checks, ribs on bosses, review questions."""
from __future__ import annotations

import review_questions
from stage2.pipeline import validate
from stage3.planner.planner import plan
from tests.fixtures import builders as b
from tests.fixtures.make_fixtures import mounting_bracket


def codes(v: dict) -> set[str]:
    return {e["code"] for e in v["errors"]}


def positions(v: dict, fid: str) -> list[tuple]:
    f = next(f for f in v["features"] if f["id"] == fid)
    return [(round(p["x"], 3), round(p["y"], 3)) if p["x"] is not None else (None, None) for p in f["instances"]]


def grid_plate(*rels) -> dict:
    return b.add_feature(b.plate(120, 60, 15), b.hole("HOLE_001", diameter=12, quantity=4), list(rels))


def test_grid_from_edge_and_spacing():
    v, _ = validate(grid_plate(b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                               b.rel("center_to_center", "", 80, "horizontal"),
                               b.rel("offset_from_edge", "top_edge", 15, "vertical"),
                               b.rel("center_to_center", "", 30, "vertical")))
    assert v["status"] == "PASS", v["errors"]
    assert positions(v, "HOLE_001") == [(20, 45), (100, 45), (20, 15), (100, 15)]


def test_grid_from_opposite_edges():
    v, _ = validate(grid_plate(b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                               b.rel("offset_from_edge", "right_edge", 20, "horizontal"),
                               b.rel("offset_from_edge", "top_edge", 15, "vertical"),
                               b.rel("offset_from_edge", "bottom_edge", 15, "vertical")))
    assert v["status"] == "PASS", v["errors"]
    assert sorted(positions(v, "HOLE_001")) == [(20, 15), (20, 45), (100, 15), (100, 45)]


def test_grid_off_the_part_is_rejected_not_forced():
    # the 20/40 pair actually dimensions ribs: 20 from the top and 40 between -> second row at y = 0
    v, _ = validate(grid_plate(b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                               b.rel("center_to_center", "", 80, "horizontal"),
                               b.rel("offset_from_edge", "top_edge", 20, "vertical"),
                               b.rel("center_to_center", "", 40, "vertical")))
    assert "PATTERN_NOT_A_GRID" in codes(v) and v["status"] == "REVIEW_REQUIRED"
    assert positions(v, "HOLE_001") == [(None, None)] * 4


def test_chain_that_does_not_add_up():
    g = b.plate(120, 60, 15)
    g["dimension_chains"] = [{"view": "TOP", "orientation": "vertical", "values": [20, 40, 20], "unit": "mm",
                              "from": "top_edge", "to": "bottom_edge"},
                             {"view": "TOP", "orientation": "horizontal", "values": [20, 80, 20], "unit": "mm",
                              "from": "left_edge", "to": "right_edge"}]
    v, _ = validate(g)
    errs = [e for e in v["errors"] if e["code"] == "DIMENSION_CHAIN_MISMATCH"]
    assert len(errs) == 1 and errs[0]["evidence"]["total"] == 80 and errs[0]["evidence"]["expected"] == 60


def test_boss_ribs_plan_and_volume():
    v, _ = validate(mounting_bracket())
    assert v["status"] == "PASS", v["errors"]
    rib = next(f for f in v["features"] if f["id"] == "RIB_001")
    assert rib["directions"] == ["+x", "-x"] and len(rib["placements"]) == 2
    p = plan(v)
    assert sum(o.operation == "extrude" and o.feature_id == "RIB_001" for o in p.operations) == 2


def test_boss_ribs_without_directions_is_a_question():
    g = mounting_bracket()
    rib = next(f for f in g["features"] if f["id"] == "RIB_001")
    rib.pop("directions")
    rib["dimensions"]["rise"]["value"] = None
    v, _ = validate(g)
    qs = {q["id"]: q for q in review_questions.build_questions(v, g)}
    assert "dirs:RIB_001" in qs and "value:RIB_001:rise" in qs
    g2, log = review_questions.apply_answers(g, {"dirs:RIB_001": qs["dirs:RIB_001"]["options"][0],
                                                 "value:RIB_001:rise": "20"})
    v2, _ = validate(g2)
    assert v2["status"] == "PASS", v2["errors"]
    assert any("directions" in line for line in log)


def test_position_question_round_trip():
    g = b.case5_missing_position()
    v, _ = validate(g)
    qs = review_questions.build_questions(v, g)
    q = next(q for q in qs if q["kind"] == "position")
    g2, log = review_questions.apply_answers(g, {q["id"]: [{"x": 30, "y": 20}]})
    v2, _ = validate(g2)
    assert v2["status"] == "PASS" and positions(v2, "HOLE_001") == [(30, 20)]
    ev = g2["relationships"][0]["relationships"][0]["evidence"]
    assert ev["source_type"] == "human_review"


def test_unsupported_feature_can_be_dropped_on_record():
    g = b.add_feature(b.plate(), {"id": "KNURL_001", "type": "knurl", "dimensions": {}})
    v, _ = validate(g)
    q = next(q for q in review_questions.build_questions(v, g) if q["id"].startswith("unsupported:"))
    g2, log = review_questions.apply_answers(g, {q["id"]: q["options"][0]})
    assert validate(g2)[0]["status"] == "PASS" and "removed" in log[0]
