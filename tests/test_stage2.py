"""Stage 2 acceptance cases + schema/unit/dimension/multiview behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stage2.pipeline import run_file, validate
from tests.fixtures import builders as b

ROOT = Path(__file__).resolve().parent.parent


def codes(v: dict) -> set[str]:
    return {e["code"] for e in v["errors"]}


def feature(v: dict, fid: str) -> dict:
    return next(f for f in v["features"] if f["id"] == fid)


# ---- acceptance cases ---------------------------------------------------------------------------

def test_case1_plate_pass():
    v, r = validate(b.case1_plate())
    assert v["status"] == "PASS", v["errors"]
    assert v["base"] == {"id": "BASE_001", "type": "rectangular_prism", "length": 120, "width": 80, "thickness": 15}
    assert r["cad_generation_allowed"] is True


def test_case2_plate_hole_pass():
    v, _ = validate(b.case2_plate_hole())
    assert v["status"] == "PASS", v["errors"]
    p = feature(v, "HOLE_001")["instances"][0]
    assert (p["x"], p["y"], p["z"]) == (20, 15, 15)
    assert "20" in p["x_source"]  # evidence preserved


def test_case3_hole_outside_fail():
    v, r = validate(b.case3_hole_outside())
    assert v["status"] == "FAIL"
    err = next(e for e in v["errors"] if e["code"] == "FEATURE_OUTSIDE_BASE")
    assert err["evidence"]["x_max"] == 128 and err["evidence"]["base_length"] == 120
    assert r["cad_generation_allowed"] is False


def test_case4_multiview_conflict_review():
    v, _ = validate(b.case4_multiview_conflict())
    assert v["status"] == "REVIEW_REQUIRED"
    err = next(e for e in v["errors"] if e["code"] == "MULTIVIEW_CONFLICT")
    assert err["evidence"]["type"] == "MULTIVIEW_CONFLICT"
    assert err["evidence"]["dimension"] == "overall_length"
    assert {(x["view"], x["value"]) for x in err["evidence"]["values"]} == {("TOP", 120), ("FRONT", 125)}
    assert v["base"]["length"] == 120  # no winner picked, nothing rewritten


def test_case5_missing_position_review():
    v, _ = validate(b.case5_missing_position())
    assert v["status"] == "REVIEW_REQUIRED"
    err = next(e for e in v["errors"] if e["code"] == "INSUFFICIENT_CONSTRAINTS")
    assert err["feature_id"] == "HOLE_001" and err["evidence"]["missing"] == ["x", "y"]
    p = feature(v, "HOLE_001")["instances"][0]
    assert p["x"] is None and p["y"] is None  # not fabricated


def test_case6_centered_boss_solver():
    v, _ = validate(b.case6_centered_boss())
    assert v["status"] == "PASS", v["errors"]
    p = feature(v, "BOSS_001")["instances"][0]
    assert (p["x"], p["y"]) == (60, 40)


def test_case7_boss_with_bore_pass():
    v, _ = validate(b.case7_boss_with_bore())
    assert v["status"] == "PASS", v["errors"]
    p = feature(v, "BORE_001")["instances"][0]
    assert (p["x"], p["y"]) == (60, 40)


# ---- schema: no silent repair ---------------------------------------------------------------------

def test_non_numeric_and_vague_quantity_are_reported_not_repaired():
    h = b.hole()
    h["dimensions"]["diameter"]["value"] = "approximately 10"
    h["quantity"] = "several"
    g = b.add_feature(b.plate(), h, [b.rel("offset_from_edge", "left_edge", 20, "horizontal"),
                                     b.rel("offset_from_edge", "bottom_edge", 15, "vertical")])
    v, _ = validate(g)
    assert v["status"] == "REVIEW_REQUIRED"
    assert {"NON_NUMERIC_VALUE", "INVALID_QUANTITY"} <= codes(v)
    f = feature(v, "HOLE_001")
    assert f["diameter"] is None and f["quantity"] is None


def test_malformed_input_fails():
    v, _ = validate(["not", "an", "object"])
    assert v["status"] == "FAIL" and "MALFORMED_INPUT" in codes(v)


def test_unsupported_feature_type_review():
    g = b.add_feature(b.plate(), {"id": "KNURL_001", "type": "knurl", "dimensions": {}})
    v, _ = validate(g)
    assert v["status"] == "REVIEW_REQUIRED" and "UNSUPPORTED_FEATURE" in codes(v)


def test_stage1_missing_information_blocks_pass():
    g = b.case2_plate_hole()
    g["missing_information"] = [{"field": "HOLE_001.depth", "reason": "x", "required_for_cad": True}]
    v, _ = validate(g)
    assert v["status"] == "REVIEW_REQUIRED" and "STAGE1_MISSING_INFORMATION" in codes(v)


# ---- units --------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,unit,expected", [(10, "mm", 10.0), (2, "cm", 20.0), (0.5, "in", 12.7),
                                                 (1, "inch", 25.4), (0.12, "m", 120.0)])
def test_unit_conversion(value, unit, expected):
    g = b.plate()
    g["features"][0]["dimensions"]["thickness"] = b.Q(value, unit=unit, view="FRONT")
    g["overall_dimensions"]["height"] = b.Q(value, unit=unit, view="FRONT")
    v, _ = validate(g)
    assert v["base"]["thickness"] == pytest.approx(expected)
    m = next(m for m in v["measurements"] if m["field"] == "BASE_001.thickness")
    assert (m["original_value"], m["original_unit"]) == (value, unit)
    assert m["normalized_value_mm"] == pytest.approx(expected)


def test_unknown_unit_not_converted():
    g = b.plate()
    g["features"][0]["dimensions"]["thickness"] = b.Q(15, unit="furlong", view="FRONT")
    g["overall_dimensions"]["height"] = None
    v, _ = validate(g)
    assert v["status"] == "REVIEW_REQUIRED" and "UNKNOWN_UNIT" in codes(v)
    assert v["base"]["thickness"] is None


def test_unit_inherited_from_drawing():
    g = b.plate(units="in")
    for q in (*g["features"][0]["dimensions"].values(), *g["overall_dimensions"].values()):
        q["unit"] = None
    v, _ = validate(g)
    assert v["base"]["length"] == pytest.approx(120 * 25.4)


def test_no_unit_anywhere_is_review():
    g = b.plate(units=None)
    g["features"][0]["dimensions"]["length"]["unit"] = None
    v, _ = validate(g)
    assert "UNIT_MISSING" in codes(v)


# ---- dimensions ---------------------------------------------------------------------------------

def test_non_positive_dimension_fails():
    v, _ = validate(b.plate(thickness=-5))
    assert v["status"] == "FAIL" and "NON_POSITIVE_DIMENSION" in codes(v)


def test_hole_larger_than_plate_fails():
    g = b.add_feature(b.plate(width=40), b.hole(diameter=60), [b.rel("centered_on_plate", "base")])
    v, _ = validate(g)
    assert v["status"] == "FAIL" and "HOLE_LARGER_THAN_BASE" in codes(v)


def test_missing_base_thickness_review():
    g = b.plate()
    g["features"][0]["dimensions"]["thickness"] = b.Q(None, view="FRONT")
    g["overall_dimensions"]["height"] = b.Q(None, view="FRONT")
    v, _ = validate(g)
    assert v["status"] == "REVIEW_REQUIRED" and "MISSING_REQUIRED_DIMENSION" in codes(v)


# ---- real Stage 1 outputs (regression) -------------------------------------------------------------

@pytest.mark.parametrize("name", ["drawing_1", "drawing_2"])
def test_real_stage1_outputs_are_review_not_pass(name, tmp_path):
    path = ROOT / "output" / name / "geometry.json"
    if not path.exists():
        pytest.skip("real Stage 1 output not present")
    v, r = run_file(path, tmp_path)
    assert v["status"] == "REVIEW_REQUIRED"  # both drawings have documented gaps
    assert (tmp_path / "validated_geometry.json").exists() and (tmp_path / "validation_report.json").exists()
    json.loads((tmp_path / "validated_geometry.json").read_text(encoding="utf-8"))
