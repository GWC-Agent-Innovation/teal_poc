"""3D model step: make the TRELLIS.2 mesh ready for SolidWorks.

TRELLIS.2 outputs a Y-up glTF mesh inside a unit cube, often with hundreds of thousands of faces.
SolidWorks needs a closed, reasonably small mesh at real size, so this step:
  1. repairs it (merge vertices, drop degenerate/duplicate faces, fill small holes, fix normals)
  2. decimates it to at most `max_faces` triangles (SolidWorks makes one B-rep face per triangle)
  3. turns it Z-up and scales it so its longest side is `size_mm`
  4. puts it on the floor: origin at the lower-left corner of its bounding box
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

log = logging.getLogger("tellis.mesh")

Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])   # glTF (x, y, z) -> (x, -z, y)
PART_TO_SOLIDWORKS = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)  # (x, y, z) -> (x, z, -y)


def load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(str(path), force="mesh", process=True)
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError(f"{path.name} contains no triangle mesh")
    return loaded


def repair(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m = trimesh.Trimesh(vertices=m.vertices, faces=m.faces, process=True)  # drop textures: geometry only
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    m.merge_vertices()
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    return m


def prepare(src: Path, size_mm: float, max_faces: int = 20_000, y_up: bool = True) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    """Return the mesh in the part frame (mm, Z-up, on the floor) and a report."""
    raw = load_mesh(src)
    report: dict[str, Any] = {"source": src.name, "faces_in": int(len(raw.faces)), "warnings": []}
    m = repair(raw)
    if len(m.faces) > max_faces:
        m = m.simplify_quadric_decimation(face_count=max_faces)
        m = repair(m)
    if y_up:
        m.apply_transform(Y_UP_TO_Z_UP)
    m.apply_scale(size_mm / float(max(m.extents)))
    m.apply_translation(-m.bounds[0])
    if not m.is_watertight:
        report["warnings"].append("mesh is not closed after repair; SolidWorks may import it as a surface body")
    report.update(faces_out=int(len(m.faces)), watertight=bool(m.is_watertight),
                  extents_mm=[round(float(v), 3) for v in m.extents],
                  volume_mm3=round(float(m.volume), 1) if m.is_watertight else None)
    log.info("Mesh: %d -> %d faces, %s mm, watertight=%s", report["faces_in"], report["faces_out"],
             " x ".join(f"{v:g}" for v in report["extents_mm"]), report["watertight"])
    return m, report


def to_solidworks_frame(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """SolidWorks is Y-up: write the STL in its model frame so the part stands upright on the Top Plane."""
    out = m.copy()
    out.apply_transform(PART_TO_SOLIDWORKS)
    return out
