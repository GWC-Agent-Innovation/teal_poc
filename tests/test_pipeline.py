"""Offline pipeline tests using a fake VLM. Run: python -m tests.test_pipeline"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from src import extractor, utils, validator
from src.main import main

Q = lambda v, raw, view="TOP", st="dimension": {  # noqa: E731
    "value": v, "unit": "mm", "evidence": {"raw_text": raw, "view": view, "source_type": st}}

GOOD = {
    "part": {"part_number": "RP-001", "part_name": "Rect plate", "units": "mm"},
    "views": [{"name": "TOP"}, "FRONT"],
    "overall_dimensions": {"length": Q(100, "100"), "width": Q(60, "60"), "height": {"value": None, "unit": "mm", "status": "missing"}},
    "features": [
        {"id": "FEATURE_001", "type": "base_plate",
         "dimensions": {"length": Q(100, "100"), "width": Q(60, "60"), "thickness": {"value": None, "unit": "mm", "status": "missing"}}},
        {"id": "HOLE_001", "type": "hole", "quantity": 4, "dimensions": {"diameter": Q(10, "4X Ø10 THRU", st="callout")},
         "depth_type": "through", "positioning": {"type": "dimension_relationships", "dimensions": []},
         "evidence": {"raw_text": "4X Ø10 THRU", "view": "TOP"}},
    ],
    "relationships": [{"feature_id": "HOLE_001", "relationships": [
        {"type": "offset_from_edge", "reference": "left_edge", "value": 20, "unit": "mm", "evidence": {"raw_text": "20"}}]}],
    "missing_information": [{"field": "FEATURE_001.thickness", "reason": "No thickness found", "required_for_cad": True}],
    "ambiguities": [{"field": "HOLE_001.position", "possible_interpretations": ["center", "tangent"], "evidence": "unclear"}],
}


class Fake:
    model = "fake-vlm"

    def __init__(self, text):
        self.text = text

    def extract(self, image, prompt):
        assert "EXTRACTOR" in prompt or "CHECKER" in prompt
        if not self.text.strip():
            raise utils.EmptyResponseError("empty")
        return self.text


def make_image(d: Path) -> Path:
    p = d / "RP-001.png"
    im = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(im).rectangle((50, 50, 350, 230), outline="black")
    im.save(p)
    return p


def run_case(text, expect_status, expect_geometry):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        img = make_image(t)
        rep = extractor.run(img, Fake(text), t / "output")
        out = t / "output" / "RP-001"
        assert rep.extraction_status == expect_status, (rep, expect_status)
        assert (out / "extraction_report.json").exists()
        assert (out / "geometry.json").exists() == expect_geometry
        assert (out / "raw_vlm_response.json").exists()  # raw always preserved
        if expect_geometry:
            g = json.loads((out / "geometry.json").read_text(encoding="utf-8"))
            assert "Ø" in g["features"][1]["dimensions"]["diameter"]["evidence"]["raw_text"]
            if expect_status == "partial":
                assert g["features"][0]["dimensions"]["thickness"]["value"] is None  # never guessed
            assert g["overall_dimensions"]["width"]["value"] == 60.0
        return rep, out


def audit_tests():
    ev = lambda raw: {"raw_text": raw, "view": "FRONT", "source_type": "dimension"}  # noqa: E731
    data = {
        "features": [{"id": "S1", "type": "slot", "dimensions": {
            "r": {"value": 10, "unit": "mm", "evidence": ev("R10")},
            "d": {"value": 10, "unit": "mm", "evidence": ev("3 × Ø10 THRU")},
            "t": {"value": 0.5, "unit": "in", "evidence": ev("1/2")},
            "a": {"value": 45, "unit": "deg", "evidence": ev("10 × 45°")},
            "w": {"value": None, "unit": "mm", "status": "missing"},
            "x": {"value": 40, "unit": "mm"}},
            "positioning": {"dimensions": [
                {"type": "offset_from_edge", "reference": "left_edge", "value": {"value": 75, "evidence": ev("25 and 50")}},
                {"type": "center_offset", "reference": "centerline", "value": 0, "evidence": ev("symmetric appearance")},
                {"type": "offset_from_edge", "reference": "top_edge", "orientation": "horizontal", "value": 25, "evidence": ev("25")},
                {"type": "offset_from_edge", "reference": "bottom_edge", "orientation": "vertical", "value": 25, "evidence": ev("25")}]}}],
    }
    flags = validator.audit(data)
    dims = data["features"][0]["dimensions"]
    assert dims["r"]["value"] == 10 and dims["d"]["value"] == 10 and dims["t"]["value"] == 0.5 and dims["a"]["value"] == 45
    rels = data["relationships"][0]["relationships"]
    assert rels[0]["value"] is None and rels[0]["rejected_value"] == 75     # summed chain rejected
    assert rels[1]["value"] is None and rels[1]["rejected_value"] == 0      # visual symmetry rejected
    assert rels[2]["value"] is None and "horizontal" in rels[2]["rejection_reason"]  # orientation conflict
    assert rels[3]["value"] == 25                                           # consistent -> kept
    fields = [m["field"] for m in data["missing_information"]]
    assert any("dimensions.w" in f for f in fields), fields                 # null auto-listed
    assert dims["x"]["value"] is None and dims["x"]["rejected_value"] == 40  # unevidenced rejected
    assert len([f for f in flags if "no printed evidence" in f or "not appear" in f or "inference" in f or "cannot measure" in f]) == 4, flags


def main_tests():
    rep, _ = run_case("```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```", "partial", True)
    assert (rep.features_detected, rep.missing_required_values, rep.ambiguities) == (2, 2, 1)  # null height auto-added to missing

    clean = json.loads(json.dumps(GOOD))
    clean.update(missing_information=[], ambiguities=[])
    clean["overall_dimensions"]["height"] = Q(5, "5")
    clean["features"][0]["dimensions"]["thickness"] = Q(5, "5")
    run_case(json.dumps(clean), "success", True)

    run_case("not json at all", "failed", False)
    run_case('{"features": [{"id": "A"}]}', "failed", False)  # schema violation (no type)
    run_case('{"features": []}', "failed", False)

    audit_tests()

    with tempfile.TemporaryDirectory() as t:
        bad = Path(t) / "x.bmp"
        bad.write_bytes(b"x")
        try:
            utils.validate_input_path(bad)
            raise AssertionError("expected InputError")
        except utils.InputError:
            pass
    assert main(["--input", "nope.png"]) == 1
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main_tests()
