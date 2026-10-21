"""Build Stage 1-shaped geometry dicts for tests (same structure src/ produces)."""
from __future__ import annotations

import copy
from typing import Any, Optional


def Q(value: Any, raw: Optional[str] = None, view: str = "TOP", unit: Optional[str] = "mm",
      source_type: str = "dimension", **extra: Any) -> dict:
    return {"value": value, "unit": unit, "status": "extracted" if value is not None else "missing",
            "evidence": {"raw_text": raw if raw is not None else (str(value) if value is not None else None),
                         "view": view, "source_type": source_type}, **extra}


def rel(type_: str, reference: str, value: Any = None, orientation: Optional[str] = None,
        instance: Optional[int] = None, view: str = "TOP", raw: Optional[str] = None, **extra: Any) -> dict:
    return {"type": type_, "reference": reference, "value": value, "unit": "mm", "orientation": orientation,
            "instance": instance, "target": extra.pop("target", "center"),
            "evidence": {"raw_text": raw if raw is not None else (str(value) if value is not None else None),
                         "view": view, "source_type": "dimension"}, **extra}


def plate(length: Any = 120, width: Any = 80, thickness: Any = 15, units: Optional[str] = "mm") -> dict:
    dims = {"length": Q(length), "width": Q(width), "thickness": Q(thickness, view="FRONT")}
    return {
        "part": {"part_number": "TEST-001", "part_name": "Test plate", "units": units},
        "views": [{"name": "TOP"}, {"name": "FRONT"}],
        "overall_dimensions": {"length": Q(length), "width": Q(width), "height": Q(thickness, view="FRONT")},
        "features": [{"id": "BASE_001", "type": "base_plate", "dimensions": dims}],
        "relationships": [], "tolerances": [], "notes": [], "missing_information": [], "ambiguities": [],
    }


def add_feature(g: dict, feature: dict, relationships: Optional[list[dict]] = None) -> dict:
    g = copy.deepcopy(g)
    g["features"].append(feature)
    if relationships:
        g["relationships"].append({"feature_id": feature["id"], "relationships": relationships})
    return g


def hole(fid: str = "HOLE_001", diameter: Any = 10, quantity: int = 1, depth_type: str = "through") -> dict:
    return {"id": fid, "type": "hole", "quantity": quantity, "depth_type": depth_type,
            "dimensions": {"diameter": Q(diameter, f"Ø{diameter} THRU", source_type="callout")},
            "evidence": {"raw_text": f"Ø{diameter} THRU", "view": "TOP", "source_type": "callout"}}


def boss(fid: str = "BOSS_001", diameter: Any = 50, height: Any = 35) -> dict:
    return {"id": fid, "type": "boss", "quantity": 1,
            "dimensions": {"diameter": Q(diameter, f"Ø{diameter}"), "height": Q(height, view="FRONT")},
            "evidence": {"raw_text": f"Ø{diameter}", "view": "TOP", "source_type": "dimension"}}


# ---- the seven acceptance cases ---------------------------------------------------------------

def case1_plate() -> dict:
    return plate()


def case2_plate_hole() -> dict:
    return add_feature(plate(), hole(), [rel("offset_from_edge", "left_edge", 20, "horizontal"),
                                         rel("offset_from_edge", "bottom_edge", 15, "vertical")])


def case3_hole_outside() -> dict:
    return add_feature(plate(), hole(diameter=20), [rel("offset_from_edge", "left_edge", 118, "horizontal"),
                                                    rel("offset_from_edge", "bottom_edge", 40, "vertical")])


def case4_multiview_conflict() -> dict:
    g = plate()
    g["overall_dimensions"]["length"] = Q(125, view="FRONT")
    return g


def case5_missing_position() -> dict:
    return add_feature(plate(), hole())


def case6_centered_boss() -> dict:
    g = add_feature(plate(), boss(), [rel("centered_on_plate", "base", None)])
    g["overall_dimensions"]["height"] = Q(50, view="FRONT")  # 15 plate + 35 boss
    return g


def case7_boss_with_bore() -> dict:
    g = case6_centered_boss()
    return add_feature(g, hole("BORE_001", diameter=30), [rel("concentric", "BOSS_001")])


CASES = {
    "case1_plate": case1_plate, "case2_plate_hole": case2_plate_hole, "case3_hole_outside": case3_hole_outside,
    "case4_multiview_conflict": case4_multiview_conflict, "case5_missing_position": case5_missing_position,
    "case6_centered_boss": case6_centered_boss, "case7_boss_with_bore": case7_boss_with_bore,
}
