"""Tellis orchestration:  2D image -> TRELLIS.2 -> 3D model -> SolidWorks.

One Job = one folder under tellis/output/<job_id>/:
    input.<ext>              the image
    trellis/mesh.glb         TRELLIS.2 output (textured, unit cube, Y-up)   (or trellis/uploaded.* to skip it)
    model.stl                repaired + decimated mesh at real size, Z-up, mm (viewer)
    model_sw.stl             the same mesh in the SolidWorks frame (what SolidWorks imports)
    mesh_report.json         faces before/after, watertight, size, volume
    cad_result.json          SolidWorks import result
    cad/part.SLDPRT|STEP|STL
    job.json                 per-stage status (read by the frontend)
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from utils.logging_config import add_file_log, remove_file_log

from . import config, mesh_prep, trellis_client

log = logging.getLogger("tellis.pipeline")

STAGES = ("trellis", "mesh", "solidworks")
STAGE_TITLES = {"trellis": "TRELLIS.2", "mesh": "3D model", "solidworks": "SolidWorks"}
FILE_KINDS = {  # relative path -> label, as listed to the frontend
    "trellis/mesh.glb": "TRELLIS.2 mesh (GLB)", "model.stl": "3D model (STL, mm)",
    "model_sw.stl": "SolidWorks import mesh (STL)", "mesh_report.json": "Mesh report",
    "cad_result.json": "SolidWorks result", "cad/part.SLDPRT": "SolidWorks part (SLDPRT)",
    "cad/part.STEP": "STEP", "cad/part.STL": "SolidWorks STL", "logs/pipeline.log": "Log",
}
LOGGERS = ["tellis", "stage3"]


def new_job_id(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem).strip("_")[:40] or "image"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}"


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except json.JSONDecodeError:
        return None


class Job:
    def __init__(self, job_dir: Path):
        self.dir = job_dir
        self.state: dict[str, Any] = _read(job_dir / "job.json") or {}

    @classmethod
    def create(cls, image: Path, size_mm: Optional[float] = None, dry_run: bool = False,
               mesh: Optional[Path] = None, max_faces: Optional[int] = None, job_id: Optional[str] = None) -> "Job":
        jid = job_id or new_job_id(image.name)
        d = config.OUTPUT / jid
        d.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(image, d / f"input{image.suffix.lower()}")
        if mesh:
            (d / "trellis").mkdir()
            shutil.copyfile(mesh, d / "trellis" / f"uploaded{mesh.suffix.lower()}")
        job = cls(d)
        job.state = {"id": jid, "created": time.time(), "image": f"input{image.suffix.lower()}",
                     "options": {"size_mm": size_mm, "dry_run": dry_run, "max_faces": max_faces,
                                 "uploaded_mesh": bool(mesh)},
                     "status": "queued", "stages": {s: {"status": "pending"} for s in STAGES}}
        job.save()
        return job

    @property
    def id(self) -> str:
        return self.state["id"]

    def save(self) -> None:
        _write(self.dir / "job.json", self.state)

    def _set(self, stage: str, status: str, message: str = "") -> None:
        st = self.state["stages"][stage]
        now = time.time()
        if status == "running":
            st.clear()
            st["started"] = now
        elif "started" in st:
            st["seconds"] = round(now - st["started"], 1)
        st.update(status=status, message=message)
        self.save()
        log.info("[%s] %s %s", stage, status, message)

    def image_path(self) -> Path:
        return self.dir / self.state["image"]

    def mesh_path(self) -> Optional[Path]:
        t = self.dir / "trellis"
        if not t.is_dir():
            return None
        return next((p for p in sorted(t.iterdir()) if p.suffix.lower() in (".glb", ".gltf", ".obj", ".ply", ".stl")), None)

    def summary(self) -> dict[str, Any]:
        files = {k: v for k, v in FILE_KINDS.items() if (self.dir / k).exists()}
        files[self.state["image"]] = "Input image"
        m = self.mesh_path()
        if m and m.name != "mesh.glb":
            files[f"trellis/{m.name}"] = "Uploaded mesh"
        return {**self.state, "files": files, "mesh_report": _read(self.dir / "mesh_report.json"),
                "cad_result": _read(self.dir / "cad_result.json")}

    # ---- stages ----------------------------------------------------------------------------------
    def _trellis(self, settings: config.Settings) -> Optional[Path]:
        existing = self.mesh_path()
        if existing is not None:
            self._set("trellis", "skipped", f"using {existing.name}")
            return existing
        client = trellis_client.create_client(settings.trellis_provider, settings.trellis_url,
                                              settings.trellis_model, settings.trellis_timeout,
                                              settings.hf_space, settings.hf_token)
        if client is None:
            self._set("trellis", "failed", "TRELLIS.2 is off (TRELLIS_PROVIDER=none): use huggingface, a GPU "
                                           "server (TRELLIS_URL) or upload a mesh")
            return None
        self._set("trellis", "running", f"{client.name} TRELLIS.2: image -> 3D mesh")
        try:
            out = client.generate(self.image_path(), self.dir / "trellis" / "mesh.glb")
        except Exception as exc:  # noqa: BLE001 - reported on the stage
            self._set("trellis", "failed", str(exc))
            return None
        self._set("trellis", "done", f"{out.stat().st_size / 1e6:.1f} MB GLB")
        return out

    def _mesh(self, src: Path, settings: config.Settings) -> bool:
        opts = self.state["options"]
        size = opts.get("size_mm") or settings.default_size_mm
        max_faces = opts.get("max_faces") or settings.max_faces
        self._set("mesh", "running", "repair, decimate, scale")
        try:
            # TRELLIS (glTF) is Y-up; STL/OBJ/PLY uploads are taken as already Z-up
            part, report = mesh_prep.prepare(src, size, max_faces, y_up=src.suffix.lower() in (".glb", ".gltf"))
            if not opts.get("size_mm"):
                report["warnings"].insert(0, f"no real size given: longest side set to {size:g} mm")
            part.export(self.dir / "model.stl")
            mesh_prep.to_solidworks_frame(part).export(self.dir / "model_sw.stl")
            _write(self.dir / "mesh_report.json", report)
        except Exception as exc:  # noqa: BLE001
            self._set("mesh", "failed", str(exc))
            return False
        dims = " x ".join(f"{v:g}" for v in report["extents_mm"])
        self._set("mesh", "done", f"{dims} mm, {report['faces_out']:,} faces"
                                  + ("" if report["watertight"] else ", NOT closed"))
        return True

    def _solidworks(self, settings: config.Settings) -> None:
        if self.state["options"].get("dry_run"):
            self._set("solidworks", "skipped", "dry run: SolidWorks not started")
            return
        from . import solidworks_import

        self._set("solidworks", "running", "importing the mesh as a solid body")
        result = solidworks_import.run(self.dir / "model_sw.stl", self.dir / "cad", settings.solidworks_visible)
        _write(self.dir / "cad_result.json", result)
        if result["status"] == "PASS":
            m = result["measured"]
            kind = "solid" if m["solid_bodies"] else "surface"
            self._set("solidworks", "done", f"{kind} body, {m['faces']:,} faces: SLDPRT + STEP exported")
        else:
            e = result["errors"][0]
            self._set("solidworks", "failed", f"{e['code']}: {e['message']}")

    # ---- run -------------------------------------------------------------------------------------
    def run(self, from_stage: str = "trellis") -> None:
        """from_stage 'mesh' re-runs the 3D model + SolidWorks steps (e.g. with a new size) without TRELLIS."""
        settings = config.load()
        handler = add_file_log(LOGGERS, self.dir / "logs" / "pipeline.log")
        self.state.update(status="running", error=None)
        for s in STAGES[STAGES.index(from_stage):]:
            self.state["stages"][s] = {"status": "pending"}
        self.save()
        try:
            if from_stage == "trellis":
                src = self._trellis(settings)
            else:
                src = self.mesh_path()
                shutil.rmtree(self.dir / "cad", ignore_errors=True)
                (self.dir / "cad_result.json").unlink(missing_ok=True)
            ok = src is not None and self._mesh(src, settings)
            if ok:
                self._solidworks(settings)
            else:
                for s in ("mesh", "solidworks"):
                    if self.state["stages"][s]["status"] == "pending":
                        self._set(s, "skipped", "no mesh")
            statuses = [self.state["stages"][s]["status"] for s in STAGES]
            self.state["status"] = "failed" if not ok else ("failed" if "failed" in statuses else "done")
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s crashed", self.id)
            self.state.update(status="failed", error=str(exc))
        finally:
            self.state["finished"] = time.time()
            self.save()
            remove_file_log(LOGGERS, handler)


def list_jobs() -> list[dict[str, Any]]:
    if not config.OUTPUT.is_dir():
        return []
    jobs = [_read(d / "job.json") for d in config.OUTPUT.iterdir() if (d / "job.json").exists()]
    return sorted((j for j in jobs if j and set(j.get("stages", {})) == set(STAGES)),
                  key=lambda j: j.get("created", 0), reverse=True)
