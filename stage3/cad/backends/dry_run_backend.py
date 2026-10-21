"""Backend that records calls instead of driving a CAD tool (dry runs and tests)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..backend import CADBackend, CADBackendError

log = logging.getLogger("stage3.cad.dry_run")


class DryRunBackend(CADBackend):
    name = "dry_run"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._open_sketches: set[str] = set()

    def _rec(self, method: str, **kw: Any) -> None:
        self.calls.append((method, kw))
        log.info("[dry-run] %s %s", method, ", ".join(f"{k}={v}" for k, v in kw.items()))

    def create_document(self, part_name: str) -> None: self._rec("create_document", part_name=part_name)
    def set_units(self, units: str) -> None: self._rec("set_units", units=units)

    def create_sketch(self, sketch_id: str, plane: str, offset: float) -> None:
        self._open_sketches.add(sketch_id)
        self._rec("create_sketch", sketch_id=sketch_id, plane=plane, offset=offset)

    def _entity(self, method: str, sketch_id: str, **kw: Any) -> None:
        if sketch_id not in self._open_sketches:
            raise CADBackendError("SKETCH_NOT_OPEN", f"{method}: sketch {sketch_id} is not open")
        self._rec(method, sketch_id=sketch_id, **kw)

    def create_rectangle(self, sketch_id, origin, length, width): self._entity("create_rectangle", sketch_id, origin=origin, length=length, width=width)
    def create_circle(self, sketch_id, center, diameter): self._entity("create_circle", sketch_id, center=center, diameter=diameter)
    def create_line(self, sketch_id, start, end): self._entity("create_line", sketch_id, start=start, end=end)
    def create_arc(self, sketch_id, center, start, end, direction): self._entity("create_arc", sketch_id, center=center, start=start, end=end, direction=direction)
    def create_slot(self, sketch_id, center, length, width, angle): self._entity("create_slot", sketch_id, center=center, length=length, width=width, angle=angle)

    def close_sketch(self, sketch_id: str) -> None:
        self._open_sketches.discard(sketch_id)
        self._rec("close_sketch", sketch_id=sketch_id)

    def extrude(self, op_id, sketch_id, distance, direction, symmetric): self._rec("extrude", op_id=op_id, sketch_id=sketch_id, distance=distance, direction=direction, symmetric=symmetric)
    def cut_extrude(self, op_id, sketch_id, extent, distance, direction): self._rec("cut_extrude", op_id=op_id, sketch_id=sketch_id, extent=extent, distance=distance, direction=direction)
    def create_hole(self, op_id, diameter, position, through, depth): self._rec("create_hole", op_id=op_id, diameter=diameter, position=position, through=through, depth=depth)
    def create_fillet(self, op_id, edge_lines, radius): self._rec("create_fillet", op_id=op_id, edges=len(edge_lines), radius=radius)
    def create_chamfer(self, op_id, edge_lines, distance, angle): self._rec("create_chamfer", op_id=op_id, edges=len(edge_lines), distance=distance, angle=angle)
    def rebuild(self) -> None: self._rec("rebuild")
    def inspect(self) -> Optional[dict[str, Any]]: return None
    def save(self, path: Path) -> None: self._rec("save", path=str(path))
    def export_step(self, path: Path) -> None: self._rec("export_step", path=str(path))
    def export_stl(self, path: Path) -> None: self._rec("export_stl", path=str(path))
    def close(self) -> None: self._rec("close")
