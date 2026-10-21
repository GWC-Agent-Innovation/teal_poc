"""CAD planner, dependency validation and dry-run execution (no SolidWorks)."""
from __future__ import annotations

import math

import pytest

from stage2.pipeline import validate
from stage3.cad.backends.dry_run_backend import DryRunBackend
from stage3.cad.executor import execute
from stage5.verifier import verify_model
from stage3.planner.operation_models import CADPlan
from stage3.planner.plan_validator import validate_plan
from stage3.planner.planner import PlanningError, plan
from tests.fixtures import builders as b


def ops(p: CADPlan) -> list[dict]:
    return [o.model_dump(exclude_none=True) for o in p.operations]


def test_plate_plan():
    v, _ = validate(b.case1_plate())
    p = plan(v)
    o = ops(p)
    assert [x["operation"] for x in o] == ["create_sketch", "rectangle", "extrude"]
    assert o[1]["length"] == 120 and o[1]["width"] == 80 and o[2]["distance"] == 15
    assert p.expected["volume_mm3"] == 120 * 80 * 15


def test_case7_boss_and_bore_plan():
    v, _ = validate(b.case7_boss_with_bore())
    assert v["status"] == "PASS"
    o = ops(plan(v))
    assert [x["operation"] for x in o] == [
        "create_sketch", "rectangle", "extrude",           # base 120x80x15
        "create_sketch", "circle", "extrude",              # boss Ø50 at (60,40) from Z=15, 35 high
        "create_sketch", "circle", "cut_extrude",          # bore Ø30 through all
    ]
    assert o[3]["offset"] == 15 and o[4]["center"] == (60, 40) and o[4]["diameter"] == 50 and o[5]["distance"] == 35
    assert o[7]["center"] == (60, 40) and o[7]["diameter"] == 30 and o[8]["extent"] == "through_all_both"
    p = plan(v)
    expected = 120 * 80 * 15 + math.pi * 25 ** 2 * 35 - math.pi * 15 ** 2 * 50
    assert p.expected["volume_mm3"] == pytest.approx(expected)
    assert p.expected["bbox"] == [0, 0, 0, 120, 80, 50]


def test_holes_grouped_into_one_through_cut():
    g = b.case2_plate_hole()
    g = b.add_feature(g, b.hole("HOLE_002"), [b.rel("offset_from_edge", "right_edge", 20, "horizontal"),
                                              b.rel("offset_from_edge", "bottom_edge", 15, "vertical")])
    o = ops(plan(validate(g)[0]))
    circles = [x for x in o if x["operation"] == "circle"]
    assert [c["center"] for c in circles] == [(20, 15), (100, 15)]
    assert len({c["sketch"] for c in circles}) == 1
    assert sum(x["operation"] == "cut_extrude" for x in o) == 1


def test_fillet_goes_last_with_corner_edges():
    g = b.add_feature(b.case2_plate_hole(), {"id": "FILLET_001", "type": "fillet", "quantity": 4,
                                             "dimensions": {"radius": b.Q(5, "R5")},
                                             "evidence": {"raw_text": "R5 CORNERS (4 PLACES)", "view": "TOP"}})
    o = ops(plan(validate(g)[0]))
    assert o[-1]["operation"] == "fillet" and o[-1]["target"] == "OP003" and len(o[-1]["edge_lines"]) == 4


def test_planner_refuses_review_required():
    v, _ = validate(b.case5_missing_position())
    with pytest.raises(PlanningError) as e:
        plan(v)
    assert e.value.code == "VALIDATION_NOT_PASSED"


def test_override_skips_unsolved_features_and_reports_them():
    v, _ = validate(b.case5_missing_position())
    p = plan(v, allow_override=True)
    assert p.development_override is True
    assert p.skipped == [{"feature_id": "HOLE_001", "type": "hole", "reason": "position not fully solved"}]
    assert [o.operation for o in p.operations] == ["create_sketch", "rectangle", "extrude"]


def test_dependency_missing_reference_fails_before_backend():
    v, _ = validate(b.case1_plate())
    d = plan(v).model_dump()
    d["operations"].append({"id": "OP004", "operation": "extrude", "profile": "OP099", "distance": 5})
    backend = DryRunBackend()
    res = execute(d, backend, out_dir=None, validated_status="PASS")  # type: ignore[arg-type]
    assert res["status"] == "FAIL" and res["errors"][0]["code"] == "MISSING_DEPENDENCY"
    assert backend.calls == []  # backend never touched


def test_wrong_dependency_type_and_forward_reference():
    d = {"part_name": "x", "source_status": "PASS", "operations": [
        {"id": "OP001", "operation": "create_sketch"},
        {"id": "OP002", "operation": "rectangle", "sketch": "OP001", "origin": [0, 0], "length": 1, "width": 1},
        {"id": "OP003", "operation": "extrude", "profile": "OP002", "distance": 1},   # profile must be a sketch
        {"id": "OP004", "operation": "fillet", "target": "OP005", "edges": "base_vertical_corners",
         "edge_lines": [[[0, 0, 0], [0, 0, 1]]], "radius": 0.1},                         # forward reference
        {"id": "OP005", "operation": "extrude", "profile": "OP001", "distance": 1}]}
    _, errors = validate_plan(d)
    assert {e["code"] for e in errors} == {"WRONG_DEPENDENCY_TYPE", "MISSING_DEPENDENCY"}


def test_invalid_numbers_rejected_by_schema():
    d = {"part_name": "x", "source_status": "PASS", "operations": [
        {"id": "OP001", "operation": "create_sketch"},
        {"id": "OP002", "operation": "circle", "sketch": "OP001", "center": [0, 0], "diameter": -3},
        {"id": "OP003", "operation": "extrude", "profile": "OP001", "distance": float("nan")}]}
    p, errors = validate_plan(d)
    assert p is None and len(errors) == 2 and all(e["code"] == "PLAN_SCHEMA_INVALID" for e in errors)


def test_unsupported_operation_rejected():
    d = {"part_name": "x", "source_status": "PASS",
         "operations": [{"id": "OP001", "operation": "run_python", "code": "import os"}]}
    p, errors = validate_plan(d)
    assert p is None and errors


def test_execute_refuses_review_status():
    v, _ = validate(b.case1_plate())
    backend = DryRunBackend()
    res = execute(plan(v), backend, out_dir=None, validated_status="REVIEW_REQUIRED")  # type: ignore[arg-type]
    assert res["status"] == "FAIL" and res["errors"][0]["code"] == "VALIDATION_NOT_PASSED" and not backend.calls


def test_dry_run_executes_in_order(tmp_path):
    v, _ = validate(b.case7_boss_with_bore())
    backend = DryRunBackend()
    res = execute(plan(v), backend, tmp_path, validated_status="PASS", export=False)
    assert res["status"] == "PASS" and res["measured"] is None
    names = [c[0] for c in backend.calls]
    assert names[:2] == ["create_document", "set_units"]
    assert names.index("close_sketch") < names.index("extrude")
    assert names[-2:] == ["rebuild", "close"]


def _measured_for(p: CADPlan, **override) -> dict:
    e = p.expected
    m = {"volume_mm3": e["volume_mm3"], "bbox": e["bbox"], "bodies": 1, "rebuild_errors": 0,
         "cylinders": [{"center": c["center"], "radius": c["radius"]} for c in e["cylinders"]]}
    m.update(override)
    return m


def test_stage5_passes_matching_model():
    v, _ = validate(b.case2_plate_hole())
    p = plan(v)
    rep = verify_model(v, p.model_dump(mode="json"), _measured_for(p))
    assert rep["status"] == "PASS", [c for c in rep["checks"] if not c["ok"]]
    assert any(c["drawing_evidence"] for c in rep["checks"])  # traced back to the drawing


def test_stage5_detects_misplaced_hole_and_wrong_size():
    v, _ = validate(b.case2_plate_hole())
    p = plan(v)
    moved = verify_model(v, p.model_dump(mode="json"), _measured_for(p, cylinders=[{"center": [20, 65], "radius": 5}]))
    assert moved["status"] == "FAIL"
    assert {c["category"] for c in moved["checks"] if not c["ok"]} >= {"hole_positions", "feature_count"}
    big = verify_model(v, p.model_dump(mode="json"), _measured_for(p, bbox=[0, 0, 0, 125, 80, 15]))
    assert big["status"] == "FAIL" and any(c["check"] == "overall length (X)" and not c["ok"] for c in big["checks"])


def test_stage5_incomplete_model_is_review():
    v, _ = validate(b.case5_missing_position())
    p = plan(v, allow_override=True)
    rep = verify_model(v, p.model_dump(mode="json"), _measured_for(p))
    assert rep["status"] == "REVIEW_REQUIRED"
