"""Tellis backend.  Run from the repo root:  python -m tellis serve   (http://127.0.0.1:8100)

Jobs run one at a time on a single worker thread: SolidWorks (COM) and the GPU are single-tenant."""
from __future__ import annotations

import logging
import queue
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .pipeline import STAGE_TITLES, STAGES, Job, list_jobs
from orchestrator import OUTPUT as ENGINEERING_OUTPUT, ROOT as REPO_ROOT, Run as EngineeringRun

log = logging.getLogger("tellis.server")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
MESH_EXT = (".glb", ".gltf", ".obj", ".ply", ".stl")
MEDIA = {".glb": "model/gltf-binary", ".stl": "model/stl", ".json": "application/json", ".log": "text/plain",
         ".txt": "text/plain", ".step": "application/step", ".sldprt": "application/octet-stream",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

app = FastAPI(title="Tellis: image -> TRELLIS.2 -> SolidWorks")
_queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
_current: dict[str, Optional[str]] = {"job": None}


def _worker() -> None:
    while True:
        kind, job_id, from_stage = _queue.get()
        _current["job"] = job_id
        try:
            if kind == "drawing":
                _run_drawing(job_id)
            elif kind == "multiview":
                _run_multiview(job_id)
            else:
                Job(config.OUTPUT / job_id).run(from_stage)
        except Exception:  # noqa: BLE001
            log.exception("job %s failed", job_id)
        finally:
            _current["job"] = None
            _queue.task_done()


threading.Thread(target=_worker, name="tellis-worker", daemon=True).start()


def _job(job_id: str) -> Job:
    d = (config.OUTPUT / job_id).resolve()
    if d.parent != config.OUTPUT.resolve() or not (d / "job.json").exists():
        raise HTTPException(404, f"job '{job_id}' not found")
    return Job(d)


def _drawing_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
        raise HTTPException(404, "drawing job not found")
    d = (config.OUTPUT / job_id).resolve()
    state = _read_json(d / "job.json") if d.parent == config.OUTPUT.resolve() else None
    if not state or state.get("workflow") != "drawing":
        raise HTTPException(404, f"drawing job '{job_id}' not found")
    return d


def _multiview_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
        raise HTTPException(404, "multiview job not found")
    d = (ENGINEERING_OUTPUT / job_id).resolve()
    state = _read_json(d / "job.json") if d.parent == ENGINEERING_OUTPUT.resolve() else None
    if not state or state.get("workflow") != "multiview":
        raise HTTPException(404, f"multiview job '{job_id}' not found")
    return d


def _read_json(path: Path) -> Any:
    import json
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): return None


def _write_json(path: Path, value: Any) -> None:
    import json
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def _run_drawing(job_id: str) -> None:
    d = _drawing_dir(job_id)
    state = _read_json(d / "job.json")
    state.update(status="running", error=None)
    for key in state["stages"]: state["stages"][key] = {"status": "pending"}
    _write_json(d / "job.json", state)
    def progress(stage: str, status: str, message: str) -> None:
        state["stages"][stage].update(status=status, message=message)
        _write_json(d / "job.json", state)
    try:
        from .drawing.pipeline import run
        opts = state["options"]
        progress("dimensions", "running", "reading drawing dimensions")
        result = run(d / state["image"], d, opts["thickness"], opts["relief"], opts.get("height_mm"),
                     solidworks=True, progress=progress)
        state["result"] = result
        state["status"] = "done" if result["status"] == "PASS" else "failed"
        if result["status"] != "PASS": state["error"] = "; ".join(e.get("message", str(e)) for e in result.get("errors") or [])
    except Exception as exc:  # noqa: BLE001
        log.exception("drawing job %s failed", job_id)
        state.update(status="failed", error=str(exc))
        for stage in state["stages"].values():
            if stage["status"] == "pending": stage.update(status="skipped", message="previous step failed")
            elif stage["status"] == "running": stage.update(status="failed", message=str(exc))
    finally:
        state["finished"] = __import__("time").time()
        _write_json(d / "job.json", state)


def _drawing_summary(job_id: str) -> dict[str, Any]:
    d = _drawing_dir(job_id)
    state = _read_json(d / "job.json")
    labels = {"overlay.png": "Trace overlay", "entities.json": "Fitted sketch entities",
              "dimensions.json": "Drawing dimensions", "dimensions_raw.txt": "Raw dimension extraction",
              "cad_plan.json": "CAD plan", "cad_result.json": "SolidWorks result",
              "cad/part.SLDPRT": "SolidWorks part (SLDPRT)", "cad/part.STEP": "STEP",
              "cad/part.STL": "STL", "sw_front.png": "SolidWorks front view",
              "sw_isometric.png": "SolidWorks isometric view"}
    files = {p: label for p, label in labels.items() if (d / p).is_file()}
    files[state["image"]] = "Input drawing"
    return {**state, "files": files, "result": state.get("result"),
            "dimensions": _read_json(d / "dimensions.json"), "cad_result": _read_json(d / "cad_result.json")}


def _multiview_summary(job_id: str) -> dict[str, Any]:
    d = _multiview_dir(job_id)
    state = _read_json(d / "job.json")
    files = sorted(str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*")
                   if p.is_file() and ".bak." not in p.name and p.name != "job.json")
    return {**state, "files": {p: p.rsplit("/", 1)[-1] for p in files},
            "pipeline": _read_json(d / "pipeline_status.json"),
            "extraction": _read_json(d / "extraction_report.json"),
            "validated": _read_json(d / "validated_geometry.json"),
            "validation_report": _read_json(d / "validation_report.json"),
            "cad_result": _read_json(d / "cad_result.json"),
            "verification": _read_json(d / "verification_report.json")}


def _run_multiview(job_id: str) -> None:
    d = _multiview_dir(job_id)
    state = _read_json(d / "job.json")
    state.update(status="running", error=None)
    _write_json(d / "job.json", state)
    def progress(stage: str, status: str, message: str) -> None:
        state["stages"][stage] = {"status": status, "message": message}
        _write_json(d / "job.json", state)
    try:
        ext = Path(state["image"]).suffix
        status = EngineeringRun(d, callback=progress).run(image=d / f"{job_id}{ext}", start="stage1")
        stages = status.get("stages", {})
        last = stages.get("stage5", {}).get("status")
        if last == "PASS": state["status"] = "done"
        elif any(x.get("status") in ("REVIEW_REQUIRED", "BLOCKED") for x in stages.values()): state["status"] = "review_required"
        else: state["status"] = "failed"
        if state["status"] == "failed":
            state["error"] = next((x.get("message") for x in reversed(list(stages.values())) if x.get("status") == "FAIL"), "Pipeline failed")
    except Exception as exc:  # noqa: BLE001
        log.exception("multiview job %s failed", job_id)
        state.update(status="failed", error=str(exc))
    finally:
        state["finished"] = __import__("time").time()
        _write_json(d / "job.json", state)


def _save_upload(up: UploadFile, allowed: tuple[str, ...], tmp: Path) -> Path:
    ext = Path(up.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"unsupported file type '{ext}' (allowed: {', '.join(allowed)})")
    p = tmp / f"{Path(up.filename).stem}{ext}"
    with p.open("wb") as f:
        shutil.copyfileobj(up.file, f)
    return p


# ---- API ------------------------------------------------------------------------------------------

@app.get("/api/meta")
def meta() -> dict[str, Any]:
    s = config.load()
    return {"stages": [{"id": k, "title": STAGE_TITLES[k]} for k in STAGES],
            "trellis_provider": s.trellis_provider, "running": _current["job"], "queued": _queue.qsize()}


@app.get("/api/jobs")
def jobs() -> list[dict[str, Any]]:
    drawings = []
    if config.OUTPUT.is_dir():
        for d in config.OUTPUT.iterdir():
            s = _read_json(d / "job.json") if d.is_dir() else None
            if s and s.get("workflow") == "drawing": drawings.append(s)
    multi = []
    if ENGINEERING_OUTPUT.is_dir():
        for d in ENGINEERING_OUTPUT.iterdir():
            s = _read_json(d / "job.json") if d.is_dir() else None
            if s and s.get("workflow") == "multiview": multi.append(s)
    return sorted([*list_jobs(), *drawings, *multi], key=lambda x: x.get("created", 0), reverse=True)


@app.post("/api/jobs")
def create_job(image: UploadFile = File(...), mesh: Optional[UploadFile] = File(None),
               size_mm: Optional[float] = Form(None), max_faces: Optional[int] = Form(None),
               dry_run: bool = Form(False)) -> dict[str, Any]:
    if size_mm is not None and size_mm <= 0:
        raise HTTPException(400, "size must be positive")
    if max_faces is not None and not 500 <= max_faces <= 200_000:
        raise HTTPException(400, "max_faces must be between 500 and 200000")
    with tempfile.TemporaryDirectory() as tmp:
        img = _save_upload(image, IMAGE_EXT, Path(tmp))
        msh = _save_upload(mesh, MESH_EXT, Path(tmp)) if mesh and mesh.filename else None
        job = Job.create(img, size_mm=size_mm, dry_run=dry_run, mesh=msh, max_faces=max_faces)
    _queue.put(("image", job.id, "trellis"))
    return job.summary()


@app.post("/api/drawings")
def create_drawing(image: UploadFile = File(...), thickness: float = Form(10.0), relief: float = Form(3.0),
                   height_mm: Optional[float] = Form(None)) -> dict[str, Any]:
    if thickness <= 0 or relief < 0 or (height_mm is not None and height_mm <= 0):
        raise HTTPException(400, "thickness and height must be positive; relief cannot be negative")
    with tempfile.TemporaryDirectory() as tmp:
        img = _save_upload(image, IMAGE_EXT, Path(tmp))
        from .pipeline import new_job_id
        jid = new_job_id(img.name)
        d = config.OUTPUT / jid
        d.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(img, d / f"input{img.suffix.lower()}")
    state = {"id": jid, "workflow": "drawing", "created": __import__("time").time(),
             "image": f"input{img.suffix.lower()}", "options": {"thickness": thickness, "relief": relief, "height_mm": height_mm},
             "status": "queued", "stages": {s: {"status": "pending"} for s in ("dimensions", "trace", "cad")}}
    _write_json(d / "job.json", state)
    _queue.put(("drawing", jid, "drawing"))
    return _drawing_summary(jid)


@app.post("/api/multiview")
def create_multiview(image: UploadFile = File(...)) -> dict[str, Any]:
    """Interpret a complete engineering sheet as multiple views of one part, then validate and build CAD."""
    with tempfile.TemporaryDirectory() as tmp:
        img = _save_upload(image, IMAGE_EXT, Path(tmp))
        from .pipeline import new_job_id
        jid = new_job_id(img.name)
        ENGINEERING_OUTPUT.mkdir(parents=True, exist_ok=True)
        d = ENGINEERING_OUTPUT / jid
        d.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(img, d / f"{jid}{img.suffix.lower()}")
        review_input = REPO_ROOT / "input"
        review_input.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(img, review_input / f"{jid}{img.suffix.lower()}")
    state = {"id": jid, "workflow": "multiview", "created": __import__("time").time(),
             "image": f"{jid}{img.suffix.lower()}", "status": "queued", "stages": {}}
    _write_json(d / "job.json", state)
    _queue.put(("multiview", jid, "stage1"))
    return _multiview_summary(jid)


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    d = (config.OUTPUT / job_id).resolve()
    s = _read_json(d / "job.json") if d.parent == config.OUTPUT.resolve() else None
    if s and s.get("workflow") == "drawing": return _drawing_summary(job_id)
    d = (ENGINEERING_OUTPUT / job_id).resolve()
    s = _read_json(d / "job.json") if d.parent == ENGINEERING_OUTPUT.resolve() else None
    if s and s.get("workflow") == "multiview": return _multiview_summary(job_id)
    return _job(job_id).summary()


@app.post("/api/jobs/{job_id}/rerun")
def rerun(job_id: str, body: dict = Body(default={})) -> dict[str, Any]:
    """Re-run the 3D model + SolidWorks steps with new options (size, face budget, dry run) without TRELLIS."""
    j = _job(job_id)
    if _current["job"] == job_id or j.state.get("status") in ("queued", "running"):
        raise HTTPException(409, "job is still running")
    opts = j.state["options"]
    if j.mesh_path() is None and not body.get("full"):
        raise HTTPException(409, "no mesh yet: run the full job first")
    if "size_mm" in body:
        v = body["size_mm"]
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            raise HTTPException(400, "size_mm must be a positive number or null")
        opts["size_mm"] = v
    if "max_faces" in body:
        v = body["max_faces"]
        if v is not None and (not isinstance(v, int) or not 500 <= v <= 200_000):
            raise HTTPException(400, "max_faces must be an integer between 500 and 200000")
        opts["max_faces"] = v
    if "dry_run" in body:
        opts["dry_run"] = bool(body["dry_run"])
    from_stage = "trellis" if body.get("full") else "mesh"
    j.state["status"] = "queued"
    j.save()
    _queue.put(("image", job_id, from_stage))
    return j.summary()


@app.get("/api/jobs/{job_id}/files/{path:path}")
def job_file(job_id: str, path: str) -> FileResponse:
    state = _read_json((config.OUTPUT / job_id) / "job.json") if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id) else None
    if state and state.get("workflow") == "drawing": d = _drawing_dir(job_id)
    else:
        multi_state = _read_json((ENGINEERING_OUTPUT / job_id) / "job.json") if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id) else None
        if multi_state and multi_state.get("workflow") == "multiview": d = _multiview_dir(job_id)
        else: d = _job(job_id).dir
    p = (d / path).resolve()
    if d.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(p, media_type=MEDIA.get(p.suffix.lower()), filename=p.name if p.suffix.lower() in
                        (".step", ".sldprt") else None)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC / "index.html")


app.mount("/static", StaticFiles(directory=config.STATIC), name="static")
