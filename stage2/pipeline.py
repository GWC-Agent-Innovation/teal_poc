"""Stage 2 orchestration: geometry.json -> validated_geometry.json + validation_report.json."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import (coordinate_solver, dimension_validator, geometry_checker, multiview_checker,
               relationship_solver, schema_validator, unit_normalizer, validation_report)
from .models import Context

log = logging.getLogger("stage2.pipeline")


def validate(raw: Any, source: str = "<memory>") -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure function: raw Stage 1 JSON -> (validated_geometry, validation_report)."""
    log.info("Stage 2 start: %s", source)
    ctx = Context()
    drawing = schema_validator.parse(raw, ctx)
    if drawing is not None:
        unit_normalizer.normalize(drawing, ctx)
        dimension_validator.validate(drawing, ctx)
        relationship_solver.map_relationships(drawing, ctx)
        coordinate_solver.solve(drawing, ctx)
        multiview_checker.check(drawing, ctx)
        geometry_checker.check(drawing, ctx)
    validated = validation_report.build_validated(drawing, ctx, source)
    report = validation_report.build_report(validated, ctx)
    for issue in ctx.issues:
        lvl = logging.WARNING if issue.status == "WARNING" else logging.ERROR
        log.log(lvl, "%s [%s/%s] %s", issue.status, issue.stage, issue.code, issue.message)
    log.info("Stage 2 end: %s (%s)", validated["status"],
             ", ".join(f"{k}={v}" for k, v in validated["validation"].items()))
    return validated, report


def run_file(geometry_path: Path, out_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw = json.loads(Path(geometry_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raw = None
        log.error("Could not read %s: %s", geometry_path, exc)
    validated, report = validate(raw, str(geometry_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (("validated_geometry.json", validated), ("validation_report.json", report)):
        path = out_dir / name
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Saved: %s", path)
    return validated, report
