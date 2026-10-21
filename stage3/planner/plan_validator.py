"""Everything that must hold before any CAD backend is opened."""
from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import ValidationError

from .dependency_validator import validate_dependencies
from .operation_models import CADPlan


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "FAIL", "stage": "plan_validator", "code": code, "message": message, **extra}


def validate_plan(plan: Union[CADPlan, dict], validated_status: Optional[str] = None,
                  allow_override: bool = False) -> tuple[Optional[CADPlan], list[dict[str, Any]]]:
    """Return (plan, errors). Execution is allowed only when errors is empty."""
    errors: list[dict[str, Any]] = []
    if isinstance(plan, dict):
        try:
            plan = CADPlan.model_validate(plan)
        except ValidationError as exc:
            for e in exc.errors():
                errors.append(_err("PLAN_SCHEMA_INVALID", f"{'.'.join(map(str, e['loc']))}: {e['msg']}"))
            return None, errors
    status = validated_status or plan.source_status
    if status != "PASS" and not allow_override:
        errors.append(_err("VALIDATION_NOT_PASSED",
                           f"validated geometry status is {status}; CAD generation requires PASS."))
    errors.extend(validate_dependencies(plan))
    return plan, errors
