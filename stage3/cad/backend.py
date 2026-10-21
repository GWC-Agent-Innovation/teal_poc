"""Abstract CAD backend. All arguments are millimetres / degrees in part coordinates
(X = length, Y = width, Z = height; sketches on "XY" use part X/Y). Backends convert internally."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional, Sequence

Pt2 = tuple[float, float]
Pt3 = tuple[float, float, float]


class CADBackendError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class CADBackend(ABC):
    name = "abstract"

    @abstractmethod
    def create_document(self, part_name: str) -> None: ...

    @abstractmethod
    def set_units(self, units: str) -> None: ...

    @abstractmethod
    def create_sketch(self, sketch_id: str, plane: str, offset: float) -> None: ...

    @abstractmethod
    def create_rectangle(self, sketch_id: str, origin: Pt2, length: float, width: float) -> None: ...

    @abstractmethod
    def create_circle(self, sketch_id: str, center: Pt2, diameter: float) -> None: ...

    @abstractmethod
    def create_line(self, sketch_id: str, start: Pt2, end: Pt2) -> None: ...

    @abstractmethod
    def create_arc(self, sketch_id: str, center: Pt2, start: Pt2, end: Pt2, direction: str) -> None: ...

    @abstractmethod
    def create_slot(self, sketch_id: str, center: Pt2, length: float, width: float, angle: float) -> None: ...

    @abstractmethod
    def close_sketch(self, sketch_id: str) -> None: ...

    @abstractmethod
    def extrude(self, op_id: str, sketch_id: str, distance: float, direction: str, symmetric: bool) -> None: ...

    @abstractmethod
    def cut_extrude(self, op_id: str, sketch_id: str, extent: str, distance: Optional[float], direction: str) -> None: ...

    @abstractmethod
    def create_hole(self, op_id: str, diameter: float, position: Pt3, through: bool, depth: Optional[float]) -> None: ...

    @abstractmethod
    def create_fillet(self, op_id: str, edge_lines: Sequence[tuple[Pt3, Pt3]], radius: float) -> None: ...

    @abstractmethod
    def create_chamfer(self, op_id: str, edge_lines: Sequence[tuple[Pt3, Pt3]], distance: float, angle: float) -> None: ...

    def revolve(self, op_id: str, sketch_id: str, axis_start: Pt2, axis_end: Pt2, angle: float) -> None:
        raise CADBackendError("UNSUPPORTED_OPERATION", f"{self.name}: revolve is not implemented yet (P1).")

    def linear_pattern(self, op_id: str, features: list[str], direction: Pt3, count: int, spacing: float) -> None:
        raise CADBackendError("UNSUPPORTED_OPERATION", f"{self.name}: linear_pattern is not implemented yet (P1).")

    def circular_pattern(self, op_id: str, features: list[str], axis_point: Pt3, axis_direction: Pt3,
                         count: int, angle: float) -> None:
        raise CADBackendError("UNSUPPORTED_OPERATION", f"{self.name}: circular_pattern is not implemented yet (P1).")

    @abstractmethod
    def rebuild(self) -> None: ...

    def inspect(self) -> Optional[dict[str, Any]]:
        """Measured model facts for verification: volume_mm3, bbox, cylinders. None if unavailable."""
        return None

    @abstractmethod
    def save(self, path: Path) -> None: ...

    @abstractmethod
    def export_step(self, path: Path) -> None: ...

    @abstractmethod
    def export_stl(self, path: Path) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
