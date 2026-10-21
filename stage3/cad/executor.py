"""Stage 4 (3B): run a validated cad_plan through a CADBackend and export the model."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..planner.operation_models import SKETCH_ENTITY_OPS, CADPlan
from ..planner.plan_validator import validate_plan
from .backend import CADBackend, CADBackendError

log = logging.getLogger("stage3.cad.executor")



def _dispatch(backend: CADBackend, op, open_sketches: set[str]) -> None:
    k = op.operation
    if k == "create_sketch":
        backend.create_sketch(op.id, op.plane, op.offset)
        open_sketches.add(op.id)
        return
    if k in SKETCH_ENTITY_OPS:
        if op.sketch not in open_sketches:
            raise CADBackendError("SKETCH_CLOSED", f"{op.id}: sketch {op.sketch} was already consumed")
        {"rectangle": lambda: backend.create_rectangle(op.sketch, op.origin, op.length, op.width),
         "circle": lambda: backend.create_circle(op.sketch, op.center, op.diameter),
         "line": lambda: backend.create_line(op.sketch, op.start, op.end),
         "arc": lambda: backend.create_arc(op.sketch, op.center, op.start, op.end, op.direction),
         "slot": lambda: backend.create_slot(op.sketch, op.center, op.length, op.width, op.angle)}[k]()
        return
    if k in ("extrude", "cut_extrude", "revolve") and op.profile in open_sketches:
        backend.close_sketch(op.profile)
        open_sketches.discard(op.profile)
    if k == "extrude":
        backend.extrude(op.id, op.profile, op.distance, op.direction, op.symmetric)
    elif k == "cut_extrude":
        backend.cut_extrude(op.id, op.profile, op.extent, op.distance, op.direction)
    elif k == "revolve":
        backend.revolve(op.id, op.profile, op.axis_start, op.axis_end, op.angle)
    elif k == "hole":
        backend.create_hole(op.id, op.diameter, op.position, op.through, op.depth)
    elif k == "fillet":
        backend.create_fillet(op.id, op.edge_lines, op.radius)
    elif k == "chamfer":
        backend.create_chamfer(op.id, op.edge_lines, op.distance, op.angle)
    elif k == "linear_pattern":
        backend.linear_pattern(op.id, op.features, op.direction, op.count, op.spacing)
    elif k == "circular_pattern":
        backend.circular_pattern(op.id, op.features, op.axis_point, op.axis_direction, op.count, op.angle)
    else:  # pragma: no cover - schema prevents this
        raise CADBackendError("UNSUPPORTED_OPERATION", f"{op.id}: unknown operation {k}")


def execute(plan_in: CADPlan | dict, backend: CADBackend, out_dir: Path, validated_status: Optional[str] = None,
            allow_override: bool = False, export: bool = True) -> dict[str, Any]:
    plan, errors = validate_plan(plan_in, validated_status, allow_override)
    if errors or plan is None:
        for e in errors:
            log.error("Plan rejected before opening %s: [%s] %s", backend.name, e["code"], e["message"])
        return {"status": "FAIL", "stage": "plan_validator", "errors": errors, "files": {}}

    files: dict[str, str] = {}
    open_sketches: set[str] = set()
    current: Optional[str] = None
    try:
        backend.create_document(plan.part_name)
        backend.set_units(plan.units)
        for op in plan.operations:
            current = op.id
            log.info("%s %s%s", op.id, op.operation, f" ({op.feature_id})" if op.feature_id else "")
            _dispatch(backend, op, open_sketches)
        current = None
        backend.rebuild()
        measured = backend.inspect()
        if export:
            out_dir.mkdir(parents=True, exist_ok=True)
            for key, name, fn in (("sldprt", "part.SLDPRT", backend.save), ("step", "part.STEP", backend.export_step),
                                  ("stl", "part.STL", backend.export_stl)):
                path = out_dir / name
                fn(path)
                files[key] = str(path)
                log.info("Saved: %s", path)
        # Whether the model is *correct* is Stage 5's job (stage5.verifier); here it only has to build.
        return {"status": "PASS", "stage": "cad_executor", "measured": measured, "files": files, "errors": []}
    except CADBackendError as exc:
        log.error("CAD backend error at %s: [%s] %s", current, exc.code, exc.message)
        return {"status": "FAIL", "stage": "cad_executor", "files": files,
                "errors": [{"code": exc.code, "message": exc.message, "operation_id": current}]}
    finally:
        try:
            backend.close()
        except Exception as exc:  # noqa: BLE001 - closing must not mask the real result
            log.warning("Backend close failed: %s", exc)
