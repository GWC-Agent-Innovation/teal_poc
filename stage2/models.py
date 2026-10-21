"""Strongly typed Stage 2 entities (all lengths in mm after normalization)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class Status(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"


Severity = Literal["WARNING", "REVIEW_REQUIRED", "FAIL"]
StageName = Literal["schema", "units", "dimensions", "relationships", "multiview", "geometry"]
STAGES: tuple[StageName, ...] = ("schema", "units", "dimensions", "relationships", "multiview", "geometry")


class ValidationIssue(BaseModel):
    status: Severity
    stage: StageName
    code: str
    message: str
    feature_id: Optional[str] = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class Measurement(BaseModel):
    """One engineering value. `value` is normalized (mm for lengths, degrees for angles)."""

    field: str
    original_value: Any = None
    original_unit: Optional[str] = None
    unit_used: Optional[str] = None  # own unit or the drawing-level unit it inherited
    value: Optional[float] = None
    kind: Literal["length", "angle"] = "length"
    stage1_status: Optional[str] = None
    tolerance: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None
    occurrences: list[dict[str, Any]] = Field(default_factory=list)  # same dimension in other views

    @property
    def view(self) -> Optional[str]:
        return (self.evidence or {}).get("view")


class Part(BaseModel):
    part_number: Optional[str] = None
    part_name: Optional[str] = None
    units: Optional[str] = None
    material: Optional[str] = None


class View(BaseModel):
    name: str
    canonical: str  # TOP | FRONT | RIGHT | LEFT | BOTTOM | BACK | SECTION | DETAIL | ISOMETRIC | OTHER


class Position(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    x_source: Optional[str] = None
    y_source: Optional[str] = None
    z_source: Optional[str] = None


class Tolerance(BaseModel):
    applies_to: Optional[str] = None
    raw_text: Optional[str] = None
    kind: Optional[str] = None


class GDnT(BaseModel):
    raw_text: str
    feature_id: Optional[str] = None


class Thread(BaseModel):
    designation: Optional[str] = None
    depth: Optional[Measurement] = None


class FeatureRelationship(BaseModel):
    """A Stage 1 relationship mapped onto a canonical kind + axis."""

    feature_id: str
    kind: Literal[
        "from_left", "from_right", "from_bottom", "from_top", "from_base_bottom",
        "centered_horizontal", "centered_vertical", "centered_on_base",
        "concentric", "aligned_with", "center_to_center",
        "linear_pattern", "circular_pattern", "equal_spacing", "symmetric",
        "coincident", "parallel", "perpendicular", "pattern_spacing",
    ]
    axis: Optional[Literal["x", "y", "z"]] = None
    instance: Optional[int] = None  # None = applies to every instance (only valid when unambiguous)
    reference_feature: Optional[str] = None
    reference_instance: Optional[int] = None
    target: Optional[str] = None  # center | edge | arc_center
    direction: Optional[int] = None  # +1 / -1 along axis for feature-to-feature offsets
    value: Optional[Measurement] = None
    count: Optional[int] = None
    angle: Optional[Measurement] = None
    view: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        v = f" {self.value.value:g} mm" if self.value and self.value.value is not None else ""
        ref = f" {self.reference_feature}" + (f"#{self.reference_instance}" if self.reference_instance else "") \
            if self.reference_feature else ""
        raw = (self.evidence or {}).get("raw_text")
        return f"{self.kind}{ref}{v}" + (f" (evidence '{raw}')" if raw else "")


class FeatureBase(BaseModel):
    id: str
    type: str
    stage1_type: str
    quantity: Optional[int] = None
    evidence: Optional[dict[str, Any]] = None
    instances: list[Position] = Field(default_factory=list)

    def measurements(self) -> list[Measurement]:
        return [v for v in self.__dict__.values() if isinstance(v, Measurement)]


class BaseFeature(FeatureBase):
    type: Literal["base"] = "base"
    shape: Literal["rectangular_prism"] = "rectangular_prism"
    length: Optional[Measurement] = None
    width: Optional[Measurement] = None
    thickness: Optional[Measurement] = None


class Hole(FeatureBase):
    type: Literal["hole"] = "hole"
    diameter: Optional[Measurement] = None
    depth: Optional[Measurement] = None
    through: Optional[bool] = None
    thread: Optional[Thread] = None


class Slot(FeatureBase):
    type: Literal["slot"] = "slot"
    length: Optional[Measurement] = None  # overall, end to end
    width: Optional[Measurement] = None
    end_radius: Optional[Measurement] = None
    center_distance: Optional[Measurement] = None  # between end-arc centres
    depth: Optional[Measurement] = None
    through: Optional[bool] = None
    orientation: Optional[Literal["along_x", "along_y"]] = None


class Pocket(FeatureBase):
    type: Literal["pocket"] = "pocket"
    length: Optional[Measurement] = None
    width: Optional[Measurement] = None
    depth: Optional[Measurement] = None
    corner_radius: Optional[Measurement] = None


class Cutout(FeatureBase):
    type: Literal["cutout"] = "cutout"
    length: Optional[Measurement] = None
    width: Optional[Measurement] = None
    corner_radius: Optional[Measurement] = None


class Boss(FeatureBase):
    type: Literal["boss"] = "boss"
    diameter: Optional[Measurement] = None
    height: Optional[Measurement] = None


class Fillet(FeatureBase):
    type: Literal["fillet"] = "fillet"
    radius: Optional[Measurement] = None
    edges: Optional[str] = None  # e.g. base_vertical_corners


class Chamfer(FeatureBase):
    type: Literal["chamfer"] = "chamfer"
    distance: Optional[Measurement] = None
    angle: Optional[Measurement] = None
    edges: Optional[str] = None


class Lug(FeatureBase):
    """Upright plate standing on the base top face (clevis ear, tab), optionally with a cross hole.

    Instance position (x, y) is the centre of the lug footprint. normal_axis is the direction of its
    thickness: "x" means the lug's face is parallel to the YZ plane."""

    type: Literal["lug"] = "lug"
    thickness: Optional[Measurement] = None
    width: Optional[Measurement] = None
    height: Optional[Measurement] = None          # base top face -> top of lug
    top_radius: Optional[Measurement] = None
    top_type: Optional[Literal["full_round", "flat", "radius"]] = None
    hole_diameter: Optional[Measurement] = None
    hole_height: Optional[Measurement] = None     # base top face -> hole centre
    normal_axis: Optional[Literal["x", "y"]] = None


class Gusset(FeatureBase):
    """Triangular rib against a lug face: vertical leg `rise` on the lug, horizontal leg `run` on the base."""

    type: Literal["gusset"] = "gusset"
    rise: Optional[Measurement] = None
    run: Optional[Measurement] = None
    thickness: Optional[Measurement] = None
    attached_to: Optional[str] = None
    side: Optional[Literal["outer", "inner", "plus", "minus"]] = None
    directions: list[Literal["+x", "-x", "+y", "-y"]] = Field(default_factory=list)  # radial ribs on a boss
    centered_on_lug: Optional[bool] = None
    placements: list[dict] = Field(default_factory=list)  # solved: x0, y0 (foot on the lug face), dir, axis


class UnsupportedFeature(FeatureBase):
    type: Literal["unsupported"] = "unsupported"
    raw: dict[str, Any] = Field(default_factory=dict)


Feature = Union[Hole, Slot, Pocket, Cutout, Boss, Lug, Gusset, Fillet, Chamfer, UnsupportedFeature]
POSITIONED_TYPES = ("hole", "slot", "pocket", "cutout", "boss", "lug")


class Drawing(BaseModel):
    """Stage 1 geometry after parsing into typed Stage 2 entities."""

    part: Part
    views: list[View] = Field(default_factory=list)
    plan_view: Optional[str] = None
    base: Optional[BaseFeature] = None
    overall: dict[str, Optional[Measurement]] = Field(default_factory=dict)
    features: list[Feature] = Field(default_factory=list)
    relationships: list[FeatureRelationship] = Field(default_factory=list)
    raw_relationships: list[dict[str, Any]] = Field(default_factory=list)
    tolerances: list[Tolerance] = Field(default_factory=list)
    gdt: list[GDnT] = Field(default_factory=list)
    chains: list[dict[str, Any]] = Field(default_factory=list)  # printed dimension chains (edge to edge)
    notes: list[str] = Field(default_factory=list)

    def feature(self, fid: str) -> Optional[Union[Feature, BaseFeature]]:
        if self.base and self.base.id == fid:
            return self.base
        return next((f for f in self.features if f.id == fid), None)

    def all_measurements(self) -> list[Measurement]:
        out: list[Measurement] = []
        for f in ([self.base] if self.base else []) + list(self.features):
            out.extend(f.measurements())
        out.extend(m for m in self.overall.values() if m is not None)
        for r in self.relationships:
            out.extend(m for m in (r.value, r.angle) if m is not None)
        return out


class Context(BaseModel):
    """Collects issues and a human-readable trace while the pipeline runs."""

    issues: list[ValidationIssue] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    conversions: list[dict[str, Any]] = Field(default_factory=list)

    def add(self, status: Severity, stage: StageName, code: str, message: str,
            feature_id: Optional[str] = None, **evidence: Any) -> ValidationIssue:
        issue = ValidationIssue(status=status, stage=stage, code=code, message=message,
                                feature_id=feature_id, evidence=evidence)
        self.issues.append(issue)
        return issue

    def stage_status(self, stage: StageName) -> Status:
        sev = {i.status for i in self.issues if i.stage == stage}
        if "FAIL" in sev:
            return Status.FAIL
        if "REVIEW_REQUIRED" in sev:
            return Status.REVIEW_REQUIRED
        return Status.PASS

    def overall_status(self) -> Status:
        statuses = {self.stage_status(s) for s in STAGES}
        if Status.FAIL in statuses:
            return Status.FAIL
        if Status.REVIEW_REQUIRED in statuses:
            return Status.REVIEW_REQUIRED
        return Status.PASS
