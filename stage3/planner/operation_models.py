"""Controlled CAD operation library. The planner may only emit these; the executor only runs these.

All lengths are millimetres, angles degrees. 2D points are (x, y) in the sketch plane, which for
plane "XY" equals part X/Y (see stage2 coordinate system).
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
OpId = Annotated[str, Field(pattern=r"^OP\d{3,}$")]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: OpId
    feature_id: Optional[str] = None  # Stage 2 feature this op builds (traceability)

    @field_validator("*", mode="after")
    @classmethod
    def _finite(cls, v):
        for x in (v if isinstance(v, (tuple, list)) else (v,)):
            if isinstance(x, float) and not math.isfinite(x):
                raise ValueError("numeric values must be finite")
        return v


# ---- sketches ------------------------------------------------------------------------------------

class CreateSketchOp(_Op):
    operation: Literal["create_sketch"] = "create_sketch"
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    offset: Finite = 0.0  # along the plane normal (for XY: Z)


class _SketchEntity(_Op):
    sketch: OpId


class RectangleOp(_SketchEntity):
    operation: Literal["rectangle"] = "rectangle"
    origin: tuple[Finite, Finite]  # lower-left corner
    length: Positive
    width: Positive


class CircleOp(_SketchEntity):
    operation: Literal["circle"] = "circle"
    center: tuple[Finite, Finite]
    diameter: Positive


class LineOp(_SketchEntity):
    operation: Literal["line"] = "line"
    start: tuple[Finite, Finite]
    end: tuple[Finite, Finite]

    @model_validator(mode="after")
    def _non_degenerate(self):
        if math.dist(self.start, self.end) <= 1e-9:
            raise ValueError("line has zero length")
        return self


class ArcOp(_SketchEntity):
    operation: Literal["arc"] = "arc"
    center: tuple[Finite, Finite]
    start: tuple[Finite, Finite]
    end: tuple[Finite, Finite]
    direction: Literal["ccw", "cw"] = "ccw"

    @model_validator(mode="after")
    def _equal_radii(self):
        if abs(math.dist(self.center, self.start) - math.dist(self.center, self.end)) > 1e-6:
            raise ValueError("arc start and end are not equidistant from the centre")
        return self


class SlotOp(_SketchEntity):
    operation: Literal["slot"] = "slot"
    center: tuple[Finite, Finite]
    length: Positive  # overall, end to end
    width: Positive
    angle: Finite = 0.0  # degrees from +X

    @model_validator(mode="after")
    def _shape(self):
        if self.width >= self.length:
            raise ValueError("slot width must be smaller than its length")
        return self


# ---- features ------------------------------------------------------------------------------------

class ExtrudeOp(_Op):
    operation: Literal["extrude"] = "extrude"
    profile: OpId  # a create_sketch op
    distance: Positive
    direction: Literal["normal", "reverse"] = "normal"
    symmetric: bool = False


class CutExtrudeOp(_Op):
    operation: Literal["cut_extrude"] = "cut_extrude"
    profile: OpId
    extent: Literal["blind", "through_all", "through_all_both"]
    distance: Optional[Positive] = None
    direction: Literal["normal", "reverse"] = "reverse"

    @model_validator(mode="after")
    def _blind_needs_distance(self):
        if self.extent == "blind" and self.distance is None:
            raise ValueError("blind cut needs a distance")
        return self


class HoleOp(_Op):
    operation: Literal["hole"] = "hole"
    diameter: Positive
    position: tuple[Finite, Finite, Finite]  # centre on the entry face
    through: bool = True
    depth: Optional[Positive] = None
    hole_type: Literal["simple"] = "simple"

    @model_validator(mode="after")
    def _depth(self):
        if not self.through and self.depth is None:
            raise ValueError("blind hole needs a depth")
        return self


class _EdgeOp(_Op):
    target: OpId  # the body-creating op whose edges are treated
    edges: Literal["base_vertical_corners"]
    edge_lines: list[tuple[tuple[Finite, Finite, Finite], tuple[Finite, Finite, Finite]]] = Field(min_length=1)


class FilletOp(_EdgeOp):
    operation: Literal["fillet"] = "fillet"
    radius: Positive


class ChamferOp(_EdgeOp):
    operation: Literal["chamfer"] = "chamfer"
    distance: Positive
    angle: Annotated[float, Field(gt=0, lt=90)] = 45.0


class RevolveOp(_Op):
    operation: Literal["revolve"] = "revolve"
    profile: OpId
    axis_start: tuple[Finite, Finite]
    axis_end: tuple[Finite, Finite]
    angle: Annotated[float, Field(gt=0, le=360)] = 360.0


class LinearPatternOp(_Op):
    operation: Literal["linear_pattern"] = "linear_pattern"
    features: list[OpId] = Field(min_length=1)
    direction: tuple[Finite, Finite, Finite]
    count: Annotated[int, Field(ge=2)]
    spacing: Positive


class CircularPatternOp(_Op):
    operation: Literal["circular_pattern"] = "circular_pattern"
    features: list[OpId] = Field(min_length=1)
    axis_point: tuple[Finite, Finite, Finite]
    axis_direction: tuple[Finite, Finite, Finite]
    count: Annotated[int, Field(ge=2)]
    angle: Annotated[float, Field(gt=0, le=360)] = 360.0


Operation = Annotated[Union[
    CreateSketchOp, RectangleOp, CircleOp, LineOp, ArcOp, SlotOp, ExtrudeOp, CutExtrudeOp, HoleOp,
    FilletOp, ChamferOp, RevolveOp, LinearPatternOp, CircularPatternOp,
], Field(discriminator="operation")]

SKETCH_ENTITY_OPS = ("rectangle", "circle", "line", "arc", "slot")
BODY_OPS = ("extrude", "revolve")
FEATURE_OPS = ("extrude", "cut_extrude", "hole", "fillet", "chamfer", "revolve")


class CADPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_name: str
    units: Literal["mm"] = "mm"
    source_status: Literal["PASS", "REVIEW_REQUIRED", "FAIL"]
    development_override: bool = False
    operations: list[Operation] = Field(min_length=1)
    expected: dict = Field(default_factory=dict)  # analytic checks (volume, bbox, cylinders) for verification
    skipped: list[dict] = Field(default_factory=list)  # features not planned (override mode only)
