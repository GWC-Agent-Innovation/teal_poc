"""Check that every operation only references earlier, compatible operations."""
from __future__ import annotations

from typing import Any

from .operation_models import BODY_OPS, SKETCH_ENTITY_OPS, CADPlan


def _err(code: str, message: str, op_id: str | None = None) -> dict[str, Any]:
    return {"status": "FAIL", "stage": "dependency_validator", "code": code, "message": message, "operation_id": op_id}


def validate_dependencies(plan: CADPlan) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: dict[str, str] = {}  # id -> operation type (only ops defined so far)
    sketch_entities: dict[str, int] = {}
    body_exists = False

    def ref(op_id: str, field: str, target: str, allowed: tuple[str, ...]) -> bool:
        if target not in seen:
            errors.append(_err("MISSING_DEPENDENCY",
                               f"{op_id}.{field} references {target}, which does not exist before it.", op_id))
            return False
        if seen[target] not in allowed:
            errors.append(_err("WRONG_DEPENDENCY_TYPE",
                               f"{op_id}.{field} must reference {allowed}, but {target} is '{seen[target]}'.", op_id))
            return False
        return True

    for op in plan.operations:
        if op.id in seen:
            errors.append(_err("DUPLICATE_OPERATION_ID", f"operation id {op.id} is used twice.", op.id))
        kind = op.operation
        if kind in SKETCH_ENTITY_OPS:
            if ref(op.id, "sketch", op.sketch, ("create_sketch",)):  # type: ignore[attr-defined]
                sketch_entities[op.sketch] = sketch_entities.get(op.sketch, 0) + 1  # type: ignore[attr-defined]
        elif kind in ("extrude", "cut_extrude", "revolve"):
            if ref(op.id, "profile", op.profile, ("create_sketch",)) and \
                    not sketch_entities.get(op.profile):  # type: ignore[attr-defined]
                errors.append(_err("EMPTY_PROFILE", f"{op.id}: sketch {op.profile} has no entities.", op.id))  # type: ignore
            if kind == "cut_extrude" and not body_exists:
                errors.append(_err("NO_BODY", f"{op.id}: cut before any solid body exists.", op.id))
        elif kind in ("fillet", "chamfer"):
            ref(op.id, "target", op.target, BODY_OPS)  # type: ignore[attr-defined]
        elif kind == "hole" and not body_exists:
            errors.append(_err("NO_BODY", f"{op.id}: hole before any solid body exists.", op.id))
        elif kind in ("linear_pattern", "circular_pattern"):
            for fid in op.features:  # type: ignore[attr-defined]
                ref(op.id, "features", fid, ("extrude", "cut_extrude", "hole", "fillet", "chamfer"))
        seen[op.id] = kind
        if kind in BODY_OPS:
            body_exists = True
    return errors
