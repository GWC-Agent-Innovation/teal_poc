"""Geometry feasibility checks."""
from __future__ import annotations

from stage2.pipeline import validate
from tests.fixtures import builders as b


def codes(v: dict) -> set[str]:
    return {e["code"] for e in v["errors"]}


def located_hole(fid: str, d: float, x: float, y: float, **kw) -> tuple[dict, list[dict]]:
    return b.hole(fid, diameter=d, **kw), [b.rel("offset_from_edge", "left_edge", x, "horizontal"),
                                           b.rel("offset_from_edge", "bottom_edge", y, "vertical")]


def plate_with(*holes) -> dict:
    g = b.plate()
    for h, rels in holes:
        g = b.add_feature(g, h, rels)
    return g


def test_hole_touching_edge_is_allowed():
    v, _ = validate(plate_with(located_hole("H1", 10, 5, 40)))
    assert v["status"] == "PASS"


def test_hole_outside_in_y_fails():
    v, _ = validate(plate_with(located_hole("H1", 10, 50, 78)))
    assert "FEATURE_OUTSIDE_BASE" in codes(v)


def test_overlapping_holes_review():
    v, _ = validate(plate_with(located_hole("H1", 10, 30, 30), located_hole("H2", 10, 36, 30)))
    assert "OVERLAPPING_HOLES" in codes(v) and v["status"] == "REVIEW_REQUIRED"


def test_duplicate_holes_review():
    v, _ = validate(plate_with(located_hole("H1", 10, 30, 30), located_hole("H2", 10, 30, 30)))
    assert "DUPLICATE_FEATURE" in codes(v)


def test_blind_hole_deeper_than_plate_fails():
    h, rels = located_hole("H1", 10, 30, 30, depth_type="blind")
    h["dimensions"]["depth"] = b.Q(20, view="FRONT")
    v, _ = validate(plate_with((h, rels)))
    assert "DEPTH_EXCEEDS_BASE" in codes(v)


def test_bore_larger_than_boss_fails():
    g = b.add_feature(b.case6_centered_boss(), b.hole("BORE", diameter=60), [b.rel("concentric", "BOSS_001")])
    v, _ = validate(g)
    assert "BORE_LARGER_THAN_BOSS" in codes(v) and v["status"] == "FAIL"


def slot(fid="SLOT_001", length=40, width=10, orientation="along_x"):
    return {"id": fid, "type": "slot", "quantity": 1, "depth_type": "through", "orientation": orientation,
            "dimensions": {"length": b.Q(length), "width": b.Q(width), "end_radius": b.Q(width / 2, f"R{width / 2:g}")}}


def test_slot_inside_passes_and_outside_fails():
    ok = b.add_feature(b.plate(), slot(), [b.rel("centered_on_plate", "base")])
    assert validate(ok)[0]["status"] == "PASS"
    bad = b.add_feature(b.plate(), slot(length=60), [b.rel("offset_from_edge", "left_edge", 10, "horizontal"),
                                                    b.rel("offset_from_edge", "bottom_edge", 40, "vertical")])
    assert "FEATURE_OUTSIDE_BASE" in codes(validate(bad)[0])


def test_impossible_slot_fails():
    g = b.add_feature(b.plate(), slot(length=10, width=20), [b.rel("centered_on_plate", "base")])
    assert "IMPOSSIBLE_SLOT" in codes(validate(g)[0])


def test_slot_without_orientation_is_review():
    g = b.add_feature(b.plate(), slot(orientation=None), [b.rel("centered_on_plate", "base")])
    assert "SLOT_ORIENTATION_UNKNOWN" in codes(validate(g)[0])


def test_hole_overlapping_slot_review():
    g = b.add_feature(b.plate(), slot(), [b.rel("centered_on_plate", "base")])
    h, rels = located_hole("H1", 10, 60, 48)
    g = b.add_feature(g, h, rels)
    assert "HOLE_SLOT_OVERLAP" in codes(validate(g)[0])


def test_hole_tangent_to_slot_is_zero_wall():
    g = b.add_feature(b.plate(), slot(), [b.rel("centered_on_plate", "base")])  # slot y 35..45
    h, rels = located_hole("H1", 10, 60, 50)                                   # hole y 45..55 -> tangent
    assert "ZERO_WALL_THICKNESS" in codes(validate(b.add_feature(g, h, rels))[0])


def test_tangent_holes_are_zero_wall():
    v, _ = validate(plate_with(located_hole("H1", 10, 30, 30), located_hole("H2", 10, 40, 30)))
    assert "ZERO_WALL_THICKNESS" in codes(v)


def corner(kind: str, size: float) -> dict:
    field = "radius" if kind == "fillet" else "distance"
    dims = {field: b.Q(size)}
    if kind == "chamfer":
        dims["angle"] = b.Q(45, unit="deg")
    return {"id": f"{kind.upper()}_001", "type": kind, "quantity": 4, "dimensions": dims,
            "evidence": {"raw_text": "CORNER (4 PLACES)", "view": "TOP"}}


def test_corner_fillet_and_chamfer_pass():
    for kind in ("fillet", "chamfer"):
        assert validate(b.add_feature(b.plate(), corner(kind, 10)))[0]["status"] == "PASS"


def test_fillet_too_large_fails():
    assert "FILLET_TOO_LARGE" in codes(validate(b.add_feature(b.plate(), corner("fillet", 45)))[0])


def test_hole_breaking_into_chamfer_fails():
    g = b.add_feature(b.plate(), corner("chamfer", 20))
    h, rels = located_hole("H1", 10, 8, 8)
    assert "HOLE_INTERSECTS_EDGE_TREATMENT" in codes(validate(b.add_feature(g, h, rels))[0])


def test_hole_clear_of_fillet_passes():
    g = b.add_feature(b.plate(), corner("fillet", 10))
    h, rels = located_hole("H1", 10, 20, 20)
    assert validate(b.add_feature(g, h, rels))[0]["status"] == "PASS"


def test_fillet_and_chamfer_on_same_corners_review():
    g = b.add_feature(b.add_feature(b.plate(), corner("fillet", 5)), corner("chamfer", 5))
    assert "CONFLICTING_EDGE_TREATMENT" in codes(validate(g)[0])
