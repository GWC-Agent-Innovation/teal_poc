"""Live SolidWorks tests. Opt-in: set RUN_SOLIDWORKS=1 (needs Windows + SolidWorks + pywin32)."""
from __future__ import annotations

import os

import pytest

from stage2.pipeline import validate
from stage3.cad.executor import execute
from stage3.planner.planner import plan
from stage5.verifier import verify_model
from tests.fixtures import builders as b
from tests.fixtures.make_fixtures import chamfer_plate, clevis_bracket, full_plate, mounting_bracket

pytestmark = pytest.mark.skipif(os.getenv("RUN_SOLIDWORKS") != "1", reason="set RUN_SOLIDWORKS=1 to run")


def build(geometry: dict, tmp_path, export: bool = False) -> dict:
    from stage3.cad.backends.solidworks_backend import SolidWorksBackend
    v, _ = validate(geometry)
    assert v["status"] == "PASS", v["errors"]
    p = plan(v)
    res = execute(p, SolidWorksBackend(), tmp_path, validated_status="PASS", export=export)
    res["verification"] = verify_model(v, p.model_dump(mode="json"), res.get("measured"))
    return res


@pytest.mark.parametrize("make", [b.case1_plate, b.case2_plate_hole, b.case7_boss_with_bore, chamfer_plate, mounting_bracket,
                                  clevis_bracket])
def test_builds_and_verifies(make, tmp_path):
    res = build(make(), tmp_path)
    assert res["status"] == "PASS", res
    assert res["verification"]["status"] == "PASS"


def test_full_plate_exports(tmp_path):
    res = build(full_plate(), tmp_path, export=True)
    assert res["status"] == "PASS", res
    for key in ("sldprt", "step", "stl"):
        assert os.path.getsize(res["files"][key]) > 0


def test_hole_operation(tmp_path):
    from stage3.cad.backends.solidworks_backend import SolidWorksBackend
    L, W, T = 60.0, 40.0, 10.0
    d = {"part_name": "hole-op", "source_status": "PASS", "operations": [
        {"id": "OP001", "operation": "create_sketch"},
        {"id": "OP002", "operation": "rectangle", "sketch": "OP001", "origin": [0, 0], "length": L, "width": W},
        {"id": "OP003", "operation": "extrude", "profile": "OP001", "distance": T},
        {"id": "OP004", "operation": "hole", "diameter": 8, "position": [15, 20, T], "through": True},
        {"id": "OP005", "operation": "hole", "diameter": 6, "position": [45, 20, T], "through": False, "depth": 4}],
        "expected": {"volume_mm3": L * W * T - 3.141592653589793 * (16 * T + 9 * 4), "bbox": [0, 0, 0, L, W, T],
                     "cylinders": [{"feature_id": "H1", "center": [15, 20], "radius": 4},
                                   {"feature_id": "H2", "center": [45, 20], "radius": 3}]}}
    res = execute(d, SolidWorksBackend(), tmp_path, export=False)
    assert res["status"] == "PASS", res
    m = res["measured"]
    assert abs(m["volume_mm3"] - d["expected"]["volume_mm3"]) < 1e-3 * d["expected"]["volume_mm3"]
    for c in d["expected"]["cylinders"]:
        assert any(abs(c["center"][0] - x["center"][0]) < 0.01 and abs(c["center"][1] - x["center"][1]) < 0.01
                   and abs(c["radius"] - x["radius"]) < 0.01 for x in m["cylinders"]), c
