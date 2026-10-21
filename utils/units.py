"""The only place unit conversions live. Canonical internal unit: millimetre."""
from __future__ import annotations

import math
from typing import Optional

_TO_MM: dict[str, float] = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0, "millimetre": 1.0, "millimetres": 1.0,
    "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0, "centimetre": 10.0, "centimetres": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "metre": 1000.0, "metres": 1000.0,
    "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4,
}
_ANGLE_UNITS = {"deg", "degree", "degrees", "°"}


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Return a canonical key for a unit string, or None if unknown/absent."""
    if unit is None:
        return None
    u = str(unit).strip().lower().rstrip(".")
    if u in _TO_MM:
        return u
    if u in _ANGLE_UNITS:
        return "deg"
    return None


def is_length_unit(unit: Optional[str]) -> bool:
    return normalize_unit(unit) in _TO_MM


def is_angle_unit(unit: Optional[str]) -> bool:
    return normalize_unit(unit) == "deg"


def to_mm(value: float, unit: str) -> float:
    """Convert a length to mm. Raises ValueError for unknown units (never guesses)."""
    key = normalize_unit(unit)
    if key is None or key not in _TO_MM:
        raise ValueError(f"unknown length unit: {unit!r}")
    return value * _TO_MM[key]


def mm_to_m(value_mm: float) -> float:
    """CAD APIs such as SolidWorks take SI metres."""
    return value_mm / 1000.0


def deg_to_rad(value_deg: float) -> float:
    return math.radians(value_deg)
