"""VLM branch: read every dimension value on the drawing (the numbers the traced shape is snapped to)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.utils import parse_json_response
from src.vlm_client import create_client

log = logging.getLogger("tellis.drawing.dimensions")

PROMPT = """You are reading a 2D mechanical drawing. List EVERY dimension annotation you can see.
Return ONLY JSON (no prose, no code fences):
{
  "units": "mm | inch",
  "overall_height": number | null,
  "overall_width": number | null,
  "thickness": number | null,
  "symmetric_about_vertical_axis": true | false,
  "dimensions": [
    {"kind": "radius | diameter | linear | angle", "value": number, "text": "as printed, e.g. R200",
     "orientation": "horizontal | vertical | aligned | null",
     "describes": "short description of what it measures, e.g. 'total height', 'mouth top width'"}
  ]
}
Rules: copy every value exactly as printed (e.g. 70.5, 283.5); list repeated values once per occurrence;
thickness is null unless a depth/thickness is written; ignore the title block and logos."""


class Dimension(BaseModel):
    kind: Literal["radius", "diameter", "linear", "angle"]
    value: float = Field(gt=0)
    text: str = ""
    orientation: Optional[str] = None
    describes: str = ""


class DrawingDimensions(BaseModel):
    units: Optional[str] = "mm"
    overall_height: Optional[float] = None
    overall_width: Optional[float] = None
    thickness: Optional[float] = None
    symmetric_about_vertical_axis: bool = False
    dimensions: list[Dimension] = Field(default_factory=list)

    @property
    def radii(self) -> list[float]:
        return sorted({d.value for d in self.dimensions if d.kind == "radius"} |
                      {d.value / 2 for d in self.dimensions if d.kind == "diameter"})


def read_dimensions(image: Path) -> tuple[DrawingDimensions, str]:
    raw = create_client().extract(image, PROMPT)
    dims = DrawingDimensions.model_validate(parse_json_response(raw))
    log.info("VLM read %d dimensions (radii %s, height %s)", len(dims.dimensions), dims.radii, dims.overall_height)
    return dims, raw
