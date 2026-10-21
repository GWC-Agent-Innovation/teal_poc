"""Tellis tests: image -> TRELLIS.2 -> 3D model -> SolidWorks, with a synthetic 'TRELLIS' mesh.
No GPU needed; the SolidWorks import test runs only with RUN_SOLIDWORKS=1."""
from __future__ import annotations

import io
import os
import time

import numpy as np
import pytest
import trimesh
from PIL import Image

from tellis import mesh_prep, pipeline


def _trellis_like(path, subdivisions: int = 6):
    """Organic, closed blob in the unit cube, Y-up (tallest along Y), like a TRELLIS.2 GLB."""
    m = trimesh.creation.icosphere(subdivisions=subdivisions)
    v = m.vertices * (1 + 0.15 * np.sin(5 * m.vertices[:, [0]]) * np.cos(4 * m.vertices[:, [1]]))
    v[:, 1] *= 1.6
    m = trimesh.Trimesh(v, m.faces)
    m.apply_scale(1 / max(m.extents))
    m.apply_translation(-m.bounds.mean(axis=0))
    m.export(path)
    return path


def _image(path):
    Image.new("RGB", (8, 8), "white").save(path)
    return path


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.config, "OUTPUT", tmp_path / "out")
    monkeypatch.setenv("TRELLIS_PROVIDER", "none")
    return tmp_path


def test_prepare_scales_decimates_and_stands_up(tmp_path):
    src = _trellis_like(tmp_path / "mesh.glb")
    part, rep = mesh_prep.prepare(src, size_mm=80, max_faces=4000)
    assert rep["faces_in"] > 80_000 and rep["faces_out"] <= 4000
    assert rep["watertight"] and part.is_watertight
    assert max(rep["extents_mm"]) == pytest.approx(80)
    assert np.argmax(part.extents) == 2          # TRELLIS Y-up height became part Z
    assert part.bounds[0] == pytest.approx([0, 0, 0], abs=1e-9)


def test_solidworks_frame_is_y_up(tmp_path):
    part, _ = mesh_prep.prepare(_trellis_like(tmp_path / "mesh.glb", 4), size_mm=50)
    sw = mesh_prep.to_solidworks_frame(part)
    assert sw.extents == pytest.approx(part.extents[[0, 2, 1]])   # part Z (height) -> SolidWorks Y (up)
    assert sw.volume == pytest.approx(part.volume)


def test_job_dry_run_and_rerun_with_new_size(out):
    job = pipeline.Job.create(_image(out / "drawing.png"), size_mm=120, dry_run=True, max_faces=3000,
                              mesh=_trellis_like(out / "m.glb", 5))
    job.run()
    s = job.summary()
    assert s["status"] == "done", s["stages"]
    assert [s["stages"][k]["status"] for k in pipeline.STAGES] == ["skipped", "done", "skipped"]
    assert {"model.stl", "model_sw.stl", "mesh_report.json"} <= set(s["files"])
    assert max(s["mesh_report"]["extents_mm"]) == pytest.approx(120)

    job.state["options"]["size_mm"] = 60
    job.run("mesh")
    assert max(job.summary()["mesh_report"]["extents_mm"]) == pytest.approx(60)


def test_job_fails_clearly_without_trellis(out):
    job = pipeline.Job.create(_image(out / "drawing.png"))
    job.run()
    s = job.summary()
    assert s["status"] == "failed"
    assert "TRELLIS.2 is off" in s["stages"]["trellis"]["message"]
    assert s["stages"]["mesh"]["status"] == "skipped"


def test_server_job_lifecycle(out):
    from fastapi.testclient import TestClient

    from tellis import server

    img = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(img, "PNG")
    mesh = _trellis_like(out / "m.glb", 4).read_bytes()
    c = TestClient(server.app)

    assert c.get("/").status_code == 200
    assert c.post("/api/jobs", files={"image": ("x.txt", b"no")}).status_code == 400
    r = c.post("/api/jobs", files={"image": ("drawing.png", img.getvalue()), "mesh": ("m.glb", mesh)},
               data={"size_mm": "60", "dry_run": "true"})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    for _ in range(200):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j["stages"]
    assert c.get(f"/api/jobs/{jid}/files/model.stl").status_code == 200
    assert c.get(f"/api/jobs/{jid}/files/../../secret").status_code == 404
    assert [x["id"] for x in c.get("/api/jobs").json()] == [jid]
    assert c.post(f"/api/jobs/{jid}/rerun", json={"size_mm": -1}).status_code == 400
    assert c.post(f"/api/jobs/{jid}/rerun", json={"max_faces": 10}).status_code == 400


@pytest.mark.skipif(os.getenv("RUN_SOLIDWORKS") != "1", reason="set RUN_SOLIDWORKS=1 to run")
def test_solidworks_imports_solid_body(out):
    job = pipeline.Job.create(_image(out / "drawing.png"), size_mm=80, max_faces=3000,
                              mesh=_trellis_like(out / "m.glb", 5))
    job.run()
    s = job.summary()
    assert s["status"] == "done", s["stages"]
    m = s["cad_result"]["measured"]
    assert m["solid_bodies"] == 1
    assert m["volume_mm3"] == pytest.approx(s["mesh_report"]["volume_mm3"], rel=1e-3)
    assert {"cad/part.SLDPRT", "cad/part.STEP"} <= set(s["files"])


def test_huggingface_client_call_sequence(tmp_path, monkeypatch):
    import gradio_client
    from gradio_client.exceptions import AppError

    from tellis.trellis_client import HuggingFaceTrellis, TrellisError

    glb = _trellis_like(tmp_path / "space_out.glb", 3)
    calls = []

    class FakeClient:
        def __init__(self, src, token=None, verbose=True):
            calls.append(("init", src, token))

        def predict(self, *args, api_name):
            calls.append(api_name)
            if api_name == "/image_to_3d" and FAIL:
                raise AppError("You have exceeded your ZeroGPU quota")
            return {"/preprocess_image": str(img), "/extract_glb": (str(glb), str(glb))}.get(api_name)

    monkeypatch.setattr(gradio_client, "Client", FakeClient)
    img = _image(tmp_path / "drawing.png")
    FAIL = False
    out = HuggingFaceTrellis(token="hf_x").generate(img, tmp_path / "out" / "mesh.glb")
    assert out.read_bytes() == glb.read_bytes()
    assert calls == [("init", "microsoft/TRELLIS.2", "hf_x"), "/start_session", "/preprocess_image",
                     "/image_to_3d", "/extract_glb"]
    FAIL = True
    with pytest.raises(TrellisError, match="HF_TOKEN"):
        HuggingFaceTrellis().generate(img, tmp_path / "out2.glb")


def test_drawing_trace_fit_plan(tmp_path):
    """Synthetic dimensioned drawing: 200 x 120 plate, R20 corners, a centred Ø40 hole, thin dimension lines."""
    import cv2

    from stage3.cad.backends.dry_run_backend import DryRunBackend
    from stage3.cad.executor import execute
    from tellis.drawing.build import build_plan, normalize_height
    from tellis.drawing.fit import fit_all
    from tellis.drawing.linework import trace

    k, ox, oy = 2.0, 100, 80                     # px per mm, page offset
    img = np.full((400, 600, 3), 255, np.uint8)
    P = lambda x, y: (int(round(ox + x * k)), int(round(oy + (120 - y) * k)))  # noqa: E731
    r = 20
    for a, b in (((r, 0), (200 - r, 0)), ((200, r), (200, 120 - r)), ((200 - r, 120), (r, 120)), ((0, 120 - r), (0, r))):
        cv2.line(img, P(*a), P(*b), (0, 0, 0), 4, cv2.LINE_AA)
    for cx, cy, a0 in ((r, r, 90), (200 - r, r, 0), (200 - r, 120 - r, 270), (r, 120 - r, 180)):
        cv2.ellipse(img, P(cx, cy), (int(r * k), int(r * k)), 0, a0, a0 + 90, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.circle(img, P(100, 60), int(20 * k), (0, 0, 0), 4, cv2.LINE_AA)
    cv2.line(img, P(0, -15), P(200, -15), (0, 0, 0), 1)             # thin dimension line + text
    cv2.putText(img, "200", P(90, -25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    path = tmp_path / "plate.png"
    cv2.imwrite(str(path), img)

    lw = trace(path, overall_height_mm=120)
    assert [lp.kind for lp in lw.loops].count("outline") == 1
    assert [lp.kind for lp in lw.loops].count("cutout") == 1
    fits = fit_all(lw, radii=[20.0])
    normalize_height(fits, 120)
    outline = fits[0]
    x0, y0, x1, y1 = outline.polygon.bounds
    assert (x1 - x0, y1 - y0) == (pytest.approx(200, abs=1.0), pytest.approx(120, abs=0.1))
    assert sum(1 for e in outline.entities if e.kind == "arc" and e.snapped == 20.0) == 4
    hole = next(f for f in fits if f.kind == "cutout").polygon
    assert hole.area == pytest.approx(np.pi * 20 ** 2, rel=0.03)
    plan = build_plan(fits, "plate", thickness=8, relief=0)
    assert execute(plan, DryRunBackend(), tmp_path / "cad", export=False)["status"] == "PASS"
    assert plan["expected"]["volume_mm3"] == pytest.approx((200 * 120 - (4 - np.pi) * 400 - np.pi * 400) * 8, rel=0.02)
