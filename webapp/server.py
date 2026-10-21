"""Local web front end for the drawing -> CAD pipeline.  Run: python -m webapp  (http://127.0.0.1:8000)"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import orchestrator
import review_questions
from orchestrator import OUTPUT, ROOT, STAGE_TITLES, STAGES, Run
from utils.logging_config import setup_console

log = logging.getLogger("webapp")
INPUT = ROOT / "input"
STATIC = Path(__file__).resolve().parent / "static"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")

app = FastAPI(title="Drawing to CAD")
_job_lock = threading.Lock()
_job: dict[str, Any] = {"part": None, "thread": None}


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).stem).strip("._") or "drawing"
    return stem[:60]


def _part_dir(part: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", part):
        raise HTTPException(400, "invalid part name")
    d = OUTPUT / part
    if not d.is_dir():
        raise HTTPException(404, f"part '{part}' not found")
    return d


def _drawing(part: str) -> Optional[Path]:
    return next((INPUT / f"{part}{e}" for e in IMAGE_EXT if (INPUT / f"{part}{e}").exists()), None)


def _read(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except json.JSONDecodeError:
        return None


def _busy() -> bool:
    t = _job.get("thread")
    return t is not None and t.is_alive()


def _start(part: str, **kwargs: Any) -> None:
    with _job_lock:
        if _busy():
            raise HTTPException(409, f"a pipeline run for '{_job['part']}' is still in progress")

        def work() -> None:
            try:
                Run(OUTPUT / part).run(**kwargs)
            except Exception:  # noqa: BLE001
                log.exception("run failed")

        t = threading.Thread(target=work, name=f"pipeline-{part}", daemon=True)
        _job.update(part=part, thread=t)
        t.start()


# ---- API ------------------------------------------------------------------------------------------

@app.get("/api/meta")
def meta() -> dict:
    return {"stages": [{"id": s, "title": STAGE_TITLES[s]} for s in STAGES], "busy": _busy(), "busy_part": _job["part"]}


@app.get("/api/parts")
def parts() -> list[dict]:
    out = []
    for d in sorted(OUTPUT.iterdir() if OUTPUT.exists() else [], key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir() or not ((d / "geometry.json").exists() or (d / "pipeline_status.json").exists()):
            continue
        st = _read(d / "pipeline_status.json") or {}
        stages = st.get("stages", {})
        last = next((stages[s]["status"] for s in reversed(STAGES) if s in stages), None)
        out.append({"part": d.name, "has_drawing": _drawing(d.name) is not None, "last_status": last,
                    "running": _busy() and _job["part"] == d.name,
                    "reviewed": (d / "geometry_reviewed.json").exists()})
    return out


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), start: bool = True) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXT:
        raise HTTPException(400, f"unsupported image type '{ext}'; use PNG/JPG/JPEG/WEBP")
    part = _safe_name(file.filename or "drawing")
    INPUT.mkdir(exist_ok=True)
    for e in IMAGE_EXT:  # one drawing per part name
        (INPUT / f"{part}{e}").unlink(missing_ok=True)
    img = INPUT / f"{part}{ext}"
    img.write_bytes(await file.read())
    (OUTPUT / part).mkdir(parents=True, exist_ok=True)
    if start:
        _start(part, image=img, start="stage1")
    return {"part": part}


@app.get("/api/parts/{part}")
def part_detail(part: str) -> dict:
    d = _part_dir(part)
    files = sorted(str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*")
                   if p.is_file() and ".bak." not in p.name)
    return {
        "part": part,
        "running": _busy() and _job["part"] == part,
        "status": _read(d / "pipeline_status.json") or {"stages": {}},
        "has_drawing": _drawing(part) is not None,
        "reviewed": (d / "geometry_reviewed.json").exists(),
        "extraction": _read(d / "extraction_report.json"),
        "validated": _read(d / "validated_geometry.json"),
        "validation_report": _read(d / "validation_report.json"),
        "plan": _read(d / "cad_plan.json"),
        "cad_result": _read(d / "cad_result.json"),
        "verification": _read(d / "verification_report.json"),
        "review_log": _read(d / "review_log.json") or [],
        "files": files,
    }


@app.post("/api/parts/{part}/run")
def run(part: str, body: dict = Body(default={})) -> dict:
    d = _part_dir(part)
    start = body.get("start", "stage2")
    if start not in ("stage1", "stage2"):
        raise HTTPException(400, "start must be stage1 or stage2")
    image = _drawing(part)
    if start == "stage1" and image is None:
        raise HTTPException(400, "no drawing image for this part")
    if start == "stage2" and not ((d / "geometry.json").exists() or (d / "geometry_reviewed.json").exists()):
        raise HTTPException(400, "no geometry yet; run Stage 1 first")
    _start(part, image=image, start=start, allow_review=bool(body.get("allow_review")),
           dry_run=bool(body.get("dry_run")))
    return {"started": True}


@app.get("/api/parts/{part}/geometry")
def get_geometry(part: str) -> dict:
    d = _part_dir(part)
    reviewed = d / "geometry_reviewed.json"
    src = reviewed if reviewed.exists() else d / "geometry.json"
    return {"source": src.name, "geometry": _read(src)}


@app.put("/api/parts/{part}/geometry")
def put_geometry(part: str, body: dict = Body(...)) -> dict:
    d = _part_dir(part)
    geometry, note = body.get("geometry"), (body.get("note") or "").strip()
    if not isinstance(geometry, dict):
        raise HTTPException(400, "geometry must be a JSON object")
    if not note:
        raise HTTPException(400, "a review note is required (what was decided and why)")
    entry = orchestrator.save_review(d, geometry, note)
    if body.get("revalidate", True):
        _start(part, start="stage2", allow_review=bool(body.get("allow_review")), dry_run=bool(body.get("dry_run")))
    return {"saved": True, "changes": entry["changes"]}


@app.get("/api/parts/{part}/questions")
def questions(part: str) -> dict:
    d = _part_dir(part)
    validated = _read(d / "validated_geometry.json")
    geometry = get_geometry(part)["geometry"]
    if not validated or not geometry:
        return {"questions": []}
    return {"questions": review_questions.build_questions(validated, geometry)}


@app.post("/api/parts/{part}/answers")
def answers(part: str, body: dict = Body(...)) -> dict:
    d = _part_dir(part)
    geometry = get_geometry(part)["geometry"]
    if not isinstance(geometry, dict):
        raise HTTPException(400, "no geometry to review yet")
    new, log_lines = review_questions.apply_answers(geometry, body.get("answers") or {})
    if not log_lines:
        raise HTTPException(400, "no answers given")
    note = (body.get("note") or "").strip() or "; ".join(log_lines)
    entry = orchestrator.save_review(d, new, note)
    _start(part, start="stage2", allow_review=bool(body.get("allow_review")), dry_run=bool(body.get("dry_run")))
    return {"saved": True, "applied": log_lines, "changes": entry["changes"]}


@app.post("/api/parts/{part}/review/discard")
def discard(part: str) -> dict:
    orchestrator.discard_review(_part_dir(part))
    return {"discarded": True}


@app.get("/api/parts/{part}/drawing")
def drawing(part: str) -> FileResponse:
    p = _drawing(part)
    if p is None:
        raise HTTPException(404, "no drawing")
    return FileResponse(p)


@app.get("/api/parts/{part}/file/{path:path}")
def part_file(part: str, path: str) -> FileResponse:
    d = _part_dir(part).resolve()
    p = (d / path).resolve()
    if d not in p.parents or not p.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(p, filename=p.name if p.suffix.upper() in (".SLDPRT", ".STEP", ".STL") else None)


@app.exception_handler(Exception)
async def crash(_, exc: Exception) -> JSONResponse:  # keep the UI informative on bugs
    log.exception("unhandled")
    return JSONResponse({"detail": f"internal error: {exc}"}, status_code=500)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def main() -> None:
    import uvicorn
    setup_console()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
