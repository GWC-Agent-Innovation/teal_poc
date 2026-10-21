"""Coordinate / relationship solver behaviour."""
from __future__ import annotations

from stage2.pipeline import validate
from tests.fixtures import builders as b


def pos(v: dict, fid: str, i: int = 0) -> tuple:
    p = next(f for f in v["features"] if f["id"] == fid)["instances"][i]
    return p["x"], p["y"]


def codes(v: dict) -> set[str]:
    return {e["code"] for e in v["errors"]}


def test_from_right_and_top_edges():
    g = b.add_feature(b.plate(), b.hole(), [b.rel("offset_from_edge", "right_edge", 10, "horizontal"),
                                            b.rel("offset_from_edge", "top_edge", 12, "vertical")])
    v, _ = validate(g)
    assert pos(v, "HOLE_001") == (110, 68) and v["status"] == "PASS"


def test_edge_target_accounts_for_radius():
    g = b.add_feature(b.plate(), b.hole(diameter=10),
                      [b.rel("offset_from_edge", "right_edge", 10, "horizontal", target="edge"),
                       b.rel("offset_from_edge", "bottom_edge", 15, "vertical")])
    v, _ = validate(g)
    assert pos(v, "HOLE_001") == (105, 15)  # 120 - 10 - r5


def test_centered_horizontal_and_vertical():
    g = b.add_feature(b.plate(), b.hole(), [b.rel("centered_horizontal", "base"),
                                            b.rel("offset_from_edge", "bottom_edge", 20, "vertical")])
    v, _ = validate(g)
    assert pos(v, "HOLE_001") == (60, 20)


def test_centerline_alignment_is_part_centre_with_warning():
    g = b.add_feature(b.plate(), b.hole(), [b.rel("aligned_with", "vertical_centerline_of_part"),
                                            b.rel("aligned_with", "horizontal_centerline_of_part")])
    v, _ = validate(g)
    assert pos(v, "HOLE_001") == (60, 40)
    assert any(w["code"] == "CENTERLINE_AS_PART_CENTER" for w in v["warnings"])


def test_chain_dimensions_between_instances():
    g = b.add_feature(b.plate(), b.hole(quantity=3), [
        b.rel("offset_from_edge", "left_edge", 25, "horizontal", instance=1),
        b.rel("center_to_center", "HOLE_001 (instance 1)", 35, "horizontal", instance=2),
        b.rel("center_to_center", "HOLE_001 (instance 2)", 35, "horizontal", instance=3),
        b.rel("offset_from_edge", "bottom_edge", 20, "vertical", raw="20 TYP"),
    ])
    v, _ = validate(g)
    assert [pos(v, "HOLE_001", i) for i in range(3)] == [(25, 20), (60, 20), (95, 20)]
    assert v["status"] == "PASS", v["errors"]


def test_relationship_for_unspecified_instance_is_ambiguous():
    g = b.add_feature(b.plate(), b.hole(quantity=2), [b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                                                      b.rel("offset_from_edge", "bottom_edge", 20, "vertical")])
    v, _ = validate(g)
    assert codes(v) & {"AMBIGUOUS_INSTANCE", "PATTERN_NOT_A_GRID"} and v["status"] == "REVIEW_REQUIRED"


def test_overconstrained_conflict():
    g = b.add_feature(b.plate(), b.hole(), [b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                                            b.rel("offset_from_edge", "right_edge", 90, "horizontal"),
                                            b.rel("offset_from_edge", "bottom_edge", 20, "vertical")])
    v, _ = validate(g)
    assert "OVERCONSTRAINED_CONFLICT" in codes(v)
    assert pos(v, "HOLE_001")[0] is None


def test_overconstrained_consistent_is_fine():
    g = b.add_feature(b.plate(), b.hole(), [b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                                            b.rel("offset_from_edge", "right_edge", 100, "horizontal"),
                                            b.rel("offset_from_edge", "bottom_edge", 20, "vertical")])
    v, _ = validate(g)
    assert pos(v, "HOLE_001") == (20, 20) and v["status"] == "PASS"


def test_only_one_axis_known_reports_evidence():
    g = b.add_feature(b.plate(), b.hole("H2"), [b.rel("offset_from_edge", "bottom_edge", 15, "vertical")])
    v, _ = validate(g)
    err = next(e for e in v["errors"] if e["code"] == "INSUFFICIENT_CONSTRAINTS")
    assert err["feature_id"] == "H2" and err["evidence"] == {"known": {"y": 15}, "missing": ["x"], "instance": 1}


def test_feature_offset_without_direction_is_review():
    g = b.add_feature(b.case2_plate_hole(), b.hole("HOLE_002"),
                      [b.rel("center_to_center", "HOLE_001", 30, "horizontal"),
                       b.rel("offset_from_edge", "bottom_edge", 15, "vertical")])
    v, _ = validate(g)
    assert "DIRECTION_UNKNOWN" in codes(v)


def test_feature_offset_with_direction():
    g = b.add_feature(b.case2_plate_hole(), b.hole("HOLE_002"),
                      [b.rel("center_to_center", "HOLE_001", 30, "horizontal", direction="+x"),
                       b.rel("aligned_with", "HOLE_001 horizontal centerline")])
    v, _ = validate(g)
    assert pos(v, "HOLE_002") == (50, 15)


def test_circular_pattern_around_boss():
    g = b.case6_centered_boss()
    g = b.add_feature(g, b.hole("PCD_001", diameter=5, quantity=4),
                      [b.rel("circular_pattern", "BOSS_001", 40, raw="4X Ø5 EQ SP ON Ø40 PCD",
                             start_angle={"value": 0, "unit": "deg"})])
    v, _ = validate(g)
    pts = [tuple(round(c, 6) for c in pos(v, "PCD_001", i)) for i in range(4)]
    assert pts == [(80, 40), (60, 60), (40, 40), (60, 20)]


def test_elevation_view_horizontal_dimension_maps_to_x():
    g = b.add_feature(b.plate(), b.hole(), [b.rel("offset_from_edge", "left_edge", 30, "horizontal", view="FRONT"),
                                            b.rel("offset_from_edge", "bottom_edge", 20, "vertical")])
    v, _ = validate(g)
    assert pos(v, "HOLE_001") == (30, 20)
