"""Web API tests (no VLM, no SolidWorks): uses a temporary output folder."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import orchestrator
from tests.fixtures import builders as b


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from webapp import server
    out, inp = tmp_path / "output", tmp_path / "input"
    out.mkdir()
    inp.mkdir()
    monkeypatch.setattr(server, "OUTPUT", out)
    monkeypatch.setattr(server, "INPUT", inp)
    monkeypatch.setattr(orchestrator, "OUTPUT", out)
    part = out / "demo"
    part.mkdir()
    (part / "geometry.json").write_text(json.dumps(b.case5_missing_position()), encoding="utf-8")
    return TestClient(server.app), part, server


def wait(server) -> None:
    t = server._job.get("thread")
    if t is not None:
        t.join(timeout=60)


def test_static_and_meta(client):
    c, _, _ = client
    assert "Drawing" in c.get("/").text
    assert [s["id"] for s in c.get("/api/meta").json()["stages"]] == ["stage1", "stage2", "stage3", "stage4", "stage5"]


def test_upload_rejects_unsupported_type(client):
    c, _, _ = client
    r = c.post("/api/upload", files={"file": ("x.bmp", b"BM", "image/bmp")})
    assert r.status_code == 400


def test_run_blocks_on_review_then_review_unblocks(client):
    c, part, server = client
    assert c.post("/api/parts/demo/run", json={"start": "stage2", "dry_run": True}).status_code == 200
    wait(server)
    d = c.get("/api/parts/demo").json()
    assert d["status"]["stages"]["stage2"]["status"] == "REVIEW_REQUIRED"
    assert d["status"]["stages"]["stage3"]["status"] == "BLOCKED"

    geom = c.get("/api/parts/demo/geometry").json()["geometry"]
    assert c.put("/api/parts/demo/geometry", json={"geometry": geom, "note": ""}).status_code == 400  # note required
    geom["relationships"] = [{"feature_id": "HOLE_001", "relationships": [
        b.rel("offset_from_edge", "left_edge", 20, "horizontal"), b.rel("offset_from_edge", "bottom_edge", 15, "vertical")]}]
    r = c.put("/api/parts/demo/geometry", json={"geometry": geom, "note": "hole located per revised drawing", "dry_run": True})
    assert r.status_code == 200 and r.json()["changes"]
    wait(server)
    d = c.get("/api/parts/demo").json()  # re-validated automatically, then dry-run CAD
    assert d["reviewed"] and d["validated"]["status"] == "PASS"
    assert d["status"]["stages"]["stage4"]["status"] == "PASS" and d["cad_result"]["files"] == {}
    assert d["review_log"][-1]["note"] == "hole located per revised drawing"


def test_file_access_is_confined_to_part(client):
    c, _, _ = client
    assert c.get("/api/parts/demo/file/geometry.json").status_code == 200
    assert c.get("/api/parts/demo/file/../../secret.txt").status_code == 404
    assert c.get("/api/parts/..%2F..%2Fx").status_code in (400, 404)
