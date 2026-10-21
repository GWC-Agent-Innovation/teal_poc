"""Runs the five stages for one part and records progress in output/<part>/pipeline_status.json.

stage1  drawing -> geometry.json                     (VLM, src/)
stage2  geometry -> validated_geometry.json          (deterministic, stage2/)
stage3  validated -> cad_plan.json                   (deterministic, stage3/planner)
stage4  cad_plan -> SolidWorks -> SLDPRT/STEP/STL    (stage3/cad)
stage5  model vs drawing -> verification_report.json (stage5/)

Human review: an edited geometry is saved as geometry_reviewed.json (with an audit trail in
review_log.json) and takes precedence over the Stage 1 geometry.json from then on.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from utils.logging_config import add_file_log, remove_file_log

log = logging.getLogger("orchestrator")
ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
STAGES = ["stage1", "stage2", "stage3", "stage4", "stage5"]
STAGE_TITLES = {"stage1": "Drawing understanding", "stage2": "Geometry validation & solver",
                "stage3": "CAD planning", "stage4": "CAD execution", "stage5": "CAD verification"}
Callback = Optional[Callable[[str, str, str], None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class Run:
    """One pipeline run for a part directory."""

    def __init__(self, part_dir: Path, callback: Callback = None):
        self.dir = Path(part_dir)
        self.cb = callback
        self.status_path = self.dir / "pipeline_status.json"
        self.state = _read(self.status_path) or {"part": self.dir.name, "stages": {}}

    def set(self, stage: str, status: str, message: str = "", **extra: Any) -> None:
        self.state["stages"][stage] = {"status": status, "message": message, "updated": _now(), **extra}
        self.state["current"] = stage if status == "RUNNING" else None
        _write(self.status_path, self.state)
        log.info("[%s] %s %s", stage, status, message)
        if self.cb:
            self.cb(stage, status, message)

    def clear_from(self, stage: str) -> None:
        for s in STAGES[STAGES.index(stage):]:
            self.state["stages"].pop(s, None)
        _write(self.status_path, self.state)

    # ---- inputs -------------------------------------------------------------------------------
    def geometry_path(self) -> Path:
        reviewed = self.dir / "geometry_reviewed.json"
        return reviewed if reviewed.exists() else self.dir / "geometry.json"

    # ---- stages -------------------------------------------------------------------------------
    def stage1(self, image: Path) -> bool:
        from src import extractor
        from src.vlm_client import create_client
        self.set("stage1", "RUNNING", f"reading {image.name} with the VLM")
        reviewed = self.dir / "geometry_reviewed.json"
        if reviewed.exists():  # a new extraction invalidates an earlier human review
            shutil.move(reviewed, self.dir / f"geometry_reviewed.{int(time.time())}.bak.json")
        report = extractor.run(image, create_client(), self.dir.parent)
        status = {"success": "PASS", "partial": "REVIEW_REQUIRED"}.get(report.extraction_status, "FAIL")
        self.set("stage1", status, f"{report.features_detected} feature(s), {report.missing_required_values} missing, "
                 f"{report.ambiguities} ambiguous" + (f"; {'; '.join(report.errors)}" if report.errors else ""))
        return status != "FAIL"

    def stage2(self) -> dict:
        from stage2.pipeline import run_file
        geom = self.geometry_path()
        self.set("stage2", "RUNNING", f"validating {geom.name}")
        h = add_file_log(["stage2"], self.dir / "logs" / "validation.log")
        try:
            validated, report = run_file(geom, self.dir)
        finally:
            remove_file_log(["stage2"], h)
        errs = report["summary"]["codes"]
        self.set("stage2", validated["status"], ", ".join(f"{k}×{v}" for k, v in errs.items()) or "all checks passed",
                 source=geom.name)
        return validated

    def stage3(self, validated: dict, allow_review: bool) -> Optional[dict]:
        from stage3.planner.planner import PlanningError, plan
        self.set("stage3", "RUNNING", "planning CAD operations")
        try:
            cad_plan = plan(validated, allow_override=allow_review)
        except PlanningError as exc:
            self.set("stage3", "FAIL", f"[{exc.code}] {exc.message}")
            return None
        data = cad_plan.model_dump(mode="json")
        _write(self.dir / "cad_plan.json", data)
        msg = f"{len(data['operations'])} operation(s)"
        if data["skipped"]:
            msg += f"; DEVELOPMENT OVERRIDE, {len(data['skipped'])} feature(s) skipped"
        self.set("stage3", "PASS" if not data["development_override"] else "REVIEW_REQUIRED", msg)
        return data

    def stage4(self, cad_plan: dict, validated_status: str, allow_review: bool, dry_run: bool) -> Optional[dict]:
        from stage3.cad.backend import CADBackendError
        from stage3.cad.executor import execute
        self.set("stage4", "RUNNING", "dry run" if dry_run else "building in SolidWorks")
        h = add_file_log(["stage3"], self.dir / "logs" / "cad_execution.log")
        try:
            if dry_run:
                from stage3.cad.backends.dry_run_backend import DryRunBackend
                backend = DryRunBackend()
            else:
                from stage3.cad.backends.solidworks_backend import SolidWorksBackend
                try:
                    backend = SolidWorksBackend()
                except CADBackendError as exc:
                    self.set("stage4", "FAIL", f"[{exc.code}] {exc.message}")
                    return None
            result = execute(cad_plan, backend, self.dir / "cad", validated_status=validated_status,
                             allow_override=allow_review, export=not dry_run)
        finally:
            remove_file_log(["stage3"], h)
        _write(self.dir / "cad_result.json", result)
        if result["status"] != "PASS":
            e = result["errors"][0] if result["errors"] else {}
            self.set("stage4", "FAIL", f"[{e.get('code')}] {e.get('message')}")
            return None
        self.set("stage4", "PASS", "dry run (no CAD files)" if dry_run else ", ".join(Path(p).name for p in result["files"].values()))
        return result

    def stage5(self, validated: dict, cad_plan: dict, cad_result: dict) -> dict:
        from stage5.render import render_views
        from stage5.verifier import verify_model
        self.set("stage5", "RUNNING", "comparing model with drawing")
        report = verify_model(validated, cad_plan, cad_result.get("measured"))
        stl = cad_result.get("files", {}).get("stl")
        if stl and Path(stl).exists():
            report["renders"] = {k: Path(v).name for k, v in render_views(Path(stl), self.dir / "cad").items()}
        _write(self.dir / "verification_report.json", report)
        failed = [c for c in report["checks"] if not c["ok"]]
        self.set("stage5", report["status"], f"{len(report['checks']) - len(failed)}/{len(report['checks'])} checks passed")
        return report

    # ---- driver -------------------------------------------------------------------------------
    def run(self, image: Optional[Path] = None, start: str = "stage1", allow_review: bool = False,
            dry_run: bool = False) -> dict:
        self.clear_from(start)
        self.state.update(running=True, allow_review=allow_review, dry_run=dry_run, started=_now())
        _write(self.status_path, self.state)
        try:
            if start == "stage1":
                if image is None or not self.stage1(image):
                    return self.state
            validated = self.stage2()
            if validated["status"] == "FAIL":
                return self.state
            if validated["status"] == "REVIEW_REQUIRED" and not allow_review:
                self.set("stage3", "BLOCKED", "waiting for human review of the validation issues")
                return self.state
            cad_plan = self.stage3(validated, allow_review)
            if cad_plan is None:
                return self.state
            result = self.stage4(cad_plan, validated["status"], allow_review, dry_run)
            if result is None:
                return self.state
            self.stage5(validated, cad_plan, result)
            return self.state
        except Exception as exc:  # noqa: BLE001 - surface any crash in the UI instead of hanging
            log.exception("pipeline crashed")
            stage = self.state.get("current") or start
            self.set(stage, "FAIL", f"internal error: {exc}")
            return self.state
        finally:
            self.state.update(running=False, finished=_now())
            _write(self.status_path, self.state)


# ---- human review ------------------------------------------------------------------------------

def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {json.dumps(a, ensure_ascii=False)[:120]} -> {json.dumps(b, ensure_ascii=False)[:120]}"]


def save_review(part_dir: Path, geometry: dict, note: str, reviewer: str = "reviewer") -> dict:
    """Store a human-reviewed geometry plus an audit entry of exactly what changed."""
    part_dir = Path(part_dir)
    current = _read(part_dir / "geometry_reviewed.json") or _read(part_dir / "geometry.json") or {}
    changes = _diff(current, geometry)
    _write(part_dir / "geometry_reviewed.json", geometry)
    audit = _read(part_dir / "review_log.json") or []
    entry = {"time": _now(), "reviewer": reviewer, "note": note, "changes": changes}
    audit.append(entry)
    _write(part_dir / "review_log.json", audit)
    log.info("Review saved for %s: %d change(s)", part_dir.name, len(changes))
    return entry


def discard_review(part_dir: Path) -> None:
    p = Path(part_dir) / "geometry_reviewed.json"
    if p.exists():
        shutil.move(p, Path(part_dir) / f"geometry_reviewed.{int(time.time())}.bak.json")
