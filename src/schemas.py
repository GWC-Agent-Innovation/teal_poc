"""Pydantic models for the Stage 1 geometry JSON.

Permissive about *extra* fields (different parts need different feature keys)
but strict about the evidence-bearing building blocks.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    raw_text: Optional[str] = None
    view: Optional[str] = None
    source_type: Optional[str] = None  # dimension | callout | note | title_block | table | symbol ...


class Quantity(BaseModel):
    """A numeric engineering value. value=None means it could not be read."""

    model_config = ConfigDict(extra="allow")
    value: Optional[float] = None
    unit: Optional[str] = None
    status: Optional[Literal["extracted", "missing", "ambiguous", "rejected"]] = None
    tolerance: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence: Optional[Evidence] = None

    @model_validator(mode="after")
    def _default_status(self) -> "Quantity":
        if self.status is None:
            self.status = "extracted" if self.value is not None else "missing"
        return self


def _coerce_quantity(v: Any) -> Any:
    """Accept bare numbers from the VLM as a Quantity."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("boolean is not a valid quantity")
    if isinstance(v, (int, float)):
        return {"value": float(v)}
    return v


class Part(BaseModel):
    model_config = ConfigDict(extra="allow")
    part_number: Optional[str] = None
    part_name: Optional[str] = None
    units: Optional[str] = None
    material: Optional[str] = None
    scale: Optional[str] = None
    projection: Optional[str] = None  # first_angle | third_angle


class View(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    view_type: Optional[str] = None  # orthographic | section | detail | isometric ...
    description: Optional[str] = None


class OverallDimensions(BaseModel):
    model_config = ConfigDict(extra="allow")
    length: Optional[Quantity] = None
    width: Optional[Quantity] = None
    height: Optional[Quantity] = None

    @field_validator("length", "width", "height", mode="before")
    @classmethod
    def _q(cls, v):
        return _coerce_quantity(v)


class Feature(BaseModel):
    """Generic feature: base_plate, hole, slot, pocket, fillet, chamfer, boss, ..."""

    model_config = ConfigDict(extra="allow")
    id: str
    type: str
    quantity: Optional[int] = None
    dimensions: dict[str, Optional[Quantity]] = Field(default_factory=dict)
    depth_type: Optional[str] = None  # through | blind | to_face | unknown
    positioning: Optional[dict[str, Any]] = None
    thread: Optional[dict[str, Any]] = None
    evidence: Optional[Evidence] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    notes: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _lift_text_fields(cls, v):
        """Models sometimes put text attributes (orientation, edges) inside `dimensions`; lift them out."""
        if isinstance(v, dict) and isinstance(v.get("dimensions"), dict):
            dims = dict(v["dimensions"])
            for k in [k for k, x in dims.items() if isinstance(x, str)]:
                v = {**v, k: v.get(k) or dims.pop(k)}
            v = {**v, "dimensions": dims}
        return v

    @field_validator("dimensions", mode="before")
    @classmethod
    def _dims(cls, v):
        if v is None:
            return {}
        if isinstance(v, dict):
            return {k: _coerce_quantity(x) for k, x in v.items()}
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, v):
        if isinstance(v, list):
            return "; ".join(str(x) for x in v)
        return v

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v):
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v


class Relationship(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    reference: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    evidence: Optional[Evidence] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class FeatureRelationships(BaseModel):
    model_config = ConfigDict(extra="allow")
    feature_id: str
    relationships: list[Relationship] = Field(default_factory=list)


class Tolerance(BaseModel):
    model_config = ConfigDict(extra="allow")
    applies_to: Optional[str] = None
    raw_text: Optional[str] = None
    kind: Optional[str] = None  # general | dimensional | geometric | fit
    evidence: Optional[Evidence] = None


class Note(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str
    affects_geometry: Optional[bool] = None
    evidence: Optional[Evidence] = None


class MissingInformation(BaseModel):
    model_config = ConfigDict(extra="allow")
    field: str
    reason: str = "not specified"
    required_for_cad: bool = True

    @model_validator(mode="before")
    @classmethod
    def _alt_keys(cls, v):
        if isinstance(v, dict):
            v = dict(v)
            if not v.get("field"):
                v["field"] = v.get("feature_id") or v.get("item") or "unspecified"
            if not v.get("reason") and v.get("description"):
                v["reason"] = str(v["description"])
        return v


class Ambiguity(BaseModel):
    model_config = ConfigDict(extra="allow")
    field: str
    possible_interpretations: list[str] = Field(default_factory=list)
    evidence: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _alt_keys(cls, v):
        """Models sometimes use feature_id/description instead of field/interpretations."""
        if isinstance(v, dict):
            v = dict(v)
            if not v.get("field"):
                v["field"] = v.get("feature_id") or v.get("item") or v.get("subject") or "unspecified"
            if not v.get("possible_interpretations") and v.get("description"):
                v["possible_interpretations"] = [str(v["description"])]
            if isinstance(v.get("possible_interpretations"), str):
                v["possible_interpretations"] = [v["possible_interpretations"]]
        return v

    @field_validator("evidence", mode="before")
    @classmethod
    def _ev(cls, v):
        if isinstance(v, dict):
            return v.get("raw_text") or str(v)
        return v


class ExtractionMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Optional[str] = None  # the model's own view: complete | partial
    source_file: Optional[str] = None
    model: Optional[str] = None
    extracted_at: Optional[str] = None


class Geometry(BaseModel):
    model_config = ConfigDict(extra="allow")
    part: Part = Field(default_factory=Part)
    views: list[View] = Field(default_factory=list)
    overall_dimensions: OverallDimensions = Field(default_factory=OverallDimensions)
    features: list[Feature] = Field(default_factory=list)
    relationships: list[FeatureRelationships] = Field(default_factory=list)
    tolerances: list[Tolerance] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadata = Field(default_factory=ExtractionMetadata)
    validation_flags: list[str] = Field(default_factory=list)  # added by src/validator.py
    review_log: list[str] = Field(default_factory=list)  # changes made by the verification pass

    @field_validator("views", mode="before")
    @classmethod
    def _views(cls, v):
        return [{"name": x} if isinstance(x, str) else x for x in (v or [])]

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, v):
        return [{"text": x} if isinstance(x, str) else x for x in (v or [])]

    @field_validator("tolerances", mode="before")
    @classmethod
    def _tols(cls, v):
        return [{"raw_text": x} if isinstance(x, str) else x for x in (v or [])]

    @model_validator(mode="after")
    def _unique_ids(self) -> "Geometry":
        ids = [f.id for f in self.features]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"Duplicate feature ids: {sorted(dup)}")
        return self


class ExtractionReport(BaseModel):
    input_file: str
    extraction_status: Literal["success", "partial", "failed"]
    features_detected: int = 0
    missing_required_values: int = 0
    ambiguities: int = 0
    rejected_values: int = 0
    validation_flags: int = 0
    verification_changes: int = 0
    errors: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    extracted_at: Optional[str] = None
