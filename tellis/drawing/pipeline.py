"""Drawing -> SolidWorks, end to end:  python -m tellis drawing --image drawing.jpg --thickness 10 --relief 3

Outputs (out_dir): dimensions.json (VLM), entities.json (fitted sketch), overlay.png (fit drawn over the
drawing), cad_plan.json, cad_result.json, cad/part.SLDPRT|STEP|STL.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .build import build_plan, entities_json, normalize_height, push_onto
from .dimensions import DrawingDimensions, read_dimensions
from .fit import fit_all
from .linework import trace
from .overlay import draw

log = logging.getLogger("tellis.drawing")


def run(image: Path, out_dir: Path, thickness: float = 10.0, relief: float = 3.0, height_mm: Optional[float] = None,
        solidworks: bool = True, reuse_dimensions: bool = True, progress=None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dims_file = out_dir / "dimensions.json"
    if reuse_dimensions and dims_file.exists():
        dims = DrawingDimensions.model_validate_json(dims_file.read_text(encoding="utf-8"))
        log.info("Reusing %s", dims_file.name)
    else:
        dims, raw = read_dimensions(image)
        dims_file.write_text(dims.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "dimensions_raw.txt").write_text(raw, encoding="utf-8")
    if progress: progress("dimensions", "done", f"overall height {dims.overall_height:g} mm" if dims.overall_height else "dimensions extracted")
    height = height_mm or dims.overall_height
    if not height:
        raise ValueError("overall height unknown: the VLM found none; pass height_mm")

    if progress: progress("trace", "running", "tracing outline and features")
    lw = trace(image, height, symmetric=dims.symmetric_about_vertical_axis)
    fits = fit_all(lw, dims.radii, symmetric=dims.symmetric_about_vertical_axis)
    normalize_height(fits, height)
    (out_dir / "entities.json").write_text(json.dumps(entities_json(fits), indent=1), encoding="utf-8")
    draw(image, lw, fits, out_dir / "overlay.png")
    if progress: progress("trace", "done", f"{len(fits)} fitted loops")

    regions = [f for f in fits if f.kind == "region"]
    if regions and relief > 0:
        push_onto(max(regions, key=lambda f: f.polygon.area), fits[0])
    plan = build_plan(fits, image.stem, thickness, relief)
    (out_dir / "cad_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    if progress: progress("cad", "running", "building the SolidWorks part")

    result: dict[str, Any] = {"status": "SKIPPED", "files": {}}
    if solidworks:
        from stage3.cad.backends.solidworks_backend import SolidWorksBackend
        from stage3.cad.executor import execute

        result = execute(plan, SolidWorksBackend(visible=True), out_dir / "cad")
        (out_dir / "cad_result.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    if progress: progress("cad", "done" if result["status"] == "PASS" else "failed", result["status"])
    snapped = sorted({a["radius"] for l in entities_json(fits) for a in l["entities"] if a.get("snapped_to_dimension")})
    return {"status": result["status"], "loops": len(fits), "entities": sum(len(f.entities) for f in fits),
            "snapped_radii": snapped, "max_deviation_mm": round(max(f.max_dev for f in fits), 2),
            "expected": plan["expected"], "measured": result.get("measured"), "errors": result.get("errors"),
            "out_dir": str(out_dir)}
