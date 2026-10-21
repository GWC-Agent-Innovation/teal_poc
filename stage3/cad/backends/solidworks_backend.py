"""SolidWorks backend over Windows COM (pywin32). All SolidWorks-specific code lives here.

Coordinate mapping (verified against SolidWorks 2026):
  part XY plane  -> SolidWorks "Top Plane"; sketch (x, y) == part (x, y)
  part (x, y, z) -> SolidWorks model (X, Y, Z) = (x, z, -y)   (SolidWorks Y is "up")
SolidWorks API lengths are metres: every conversion goes through utils.units.mm_to_m.
"""
from __future__ import annotations

import glob
import logging
import math
import os
from pathlib import Path
from typing import Any, Optional, Sequence

from utils.units import deg_to_rad, mm_to_m

from ..backend import CADBackend, CADBackendError, Pt2, Pt3

log = logging.getLogger("stage3.cad.solidworks")

# swconst values (read from swconst.tlb)
SW_DOC_PART = 1
SW_DEFAULT_TEMPLATE_PART = 8          # swUserPreferenceStringValue_e.swDefaultTemplatePart
SW_UNIT_SYSTEM = 263                  # swUserPreferenceIntegerValue_e.swUnitSystem
SW_UNIT_SYSTEM_MMGS = 5
END_BLIND, END_THROUGH_ALL, END_MIDPLANE = 0, 1, 6
REF_PLANE_DISTANCE, REF_PLANE_FLIP = 8, 256
FILLET_OPTIONS = 195                  # propagate | uniform radius | attach edges | keep features
CHAMFER_ANGLE_DISTANCE = 1
SAVE_SILENT = 1
STL_BINARY, STL_DONT_TRANSLATE = 69, 71   # swUserPreferenceToggle_e
STL_QUALITY, STL_QUALITY_FINE = 78, 2     # swUserPreferenceIntegerValue_e / swSTLQuality_e
EPS_M = 1e-7


def _m(p: Pt3) -> tuple[float, float, float]:
    """part mm -> SolidWorks model metres."""
    x, y, z = p
    return mm_to_m(x), mm_to_m(z), -mm_to_m(y)


def _call(obj: Any, name: str, *args: Any) -> Any:
    """Late-bound COM cannot tell zero-arg methods from properties; force method semantics."""
    obj._FlagAsMethod(name)
    return getattr(obj, name)(*args)


class SolidWorksBackend(CADBackend):
    name = "solidworks"

    def __init__(self, visible: bool = True, template: Optional[str] = None):
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise CADBackendError("SOLIDWORKS_UNAVAILABLE", "pywin32 is not installed (pip install pywin32).") from exc
        self._pythoncom, self._win32 = pythoncom, win32com.client
        pythoncom.CoInitialize()
        self._none = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        self._empty = lambda: win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [])
        self._visible = visible
        self._template = template or os.getenv("SW_PART_TEMPLATE")
        self.sw: Any = None
        self.doc: Any = None
        self._sketch_names: dict[str, str] = {}
        self._planes: dict[tuple[str, float], str] = {}

    # ---- document --------------------------------------------------------------------------------
    def _connect(self) -> None:
        try:
            self.sw = self._win32.Dispatch("SldWorks.Application")
            self.sw.Visible = self._visible
        except Exception as exc:  # noqa: BLE001
            raise CADBackendError("SOLIDWORKS_UNAVAILABLE", f"cannot start/attach SolidWorks via COM: {exc}") from exc
        log.info("Connected to SolidWorks %s", _call(self.sw, "RevisionNumber"))

    def _resolve_template(self) -> str:
        candidates = [self._template, self.sw.GetUserPreferenceStringValue(SW_DEFAULT_TEMPLATE_PART)]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        found = sorted(glob.glob(r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS *\templates\**\*.prtdot", recursive=True))
        plain = [f for f in found if "MBD" not in f]
        pick = plain[0] if plain else next((f for f in found if "0051mm to 0250mm" in f), found[0] if found else None)
        if not pick:
            raise CADBackendError("TEMPLATE_NOT_FOUND", "no SolidWorks part template found; set SW_PART_TEMPLATE.")
        log.info("Using part template: %s", pick)
        return pick

    def create_document(self, part_name: str) -> None:
        self._connect()
        self.doc = self.sw.NewDocument(self._resolve_template(), 0, 0, 0)
        if self.doc is None:
            raise CADBackendError("DOCUMENT_FAILED", "SolidWorks could not create a new part document.")
        self._ext, self._sm, self._fm = self.doc.Extension, self.doc.SketchManager, self.doc.FeatureManager

    def set_units(self, units: str) -> None:
        if units != "mm":
            raise CADBackendError("UNSUPPORTED_UNITS", f"plan units must be mm, got {units}")
        if not self._ext.SetUserPreferenceInteger(SW_UNIT_SYSTEM, 0, SW_UNIT_SYSTEM_MMGS):
            log.warning("Could not switch document to MMGS (display only; API values are metres).")

    # ---- sketches --------------------------------------------------------------------------------
    def _select(self, name: str, kind: str, append: bool = False) -> None:
        if not self._ext.SelectByID2(name, kind, 0, 0, 0, append, 0, self._none, 0):
            raise CADBackendError("SELECTION_FAILED", f"could not select {kind} '{name}'")

    def _plane_for(self, plane: str, offset: float) -> str:
        """Part plane + offset -> SolidWorks plane name (creating an offset reference plane if needed).

        Verified mapping: XY = Top Plane (+offset -> part +Z); YZ = Right Plane (+offset -> part +X), sketch
        (u, v) = part (y, z); XZ = Front Plane whose normal is model +Z = part -Y, so part +Y needs the flip
        flag; sketch (u, v) = part (x, z)."""
        base = {"XY": "Top Plane", "XZ": "Front Plane", "YZ": "Right Plane"}[plane]
        if abs(offset) < 1e-9:
            return base
        key = (plane, round(offset, 6))
        if key not in self._planes:
            self.doc.ClearSelection2(True)
            self._select(base, "PLANE")
            negative_side = offset > 0 if plane == "XZ" else offset < 0
            flags = REF_PLANE_DISTANCE | (REF_PLANE_FLIP if negative_side else 0)
            feat = self._fm.InsertRefPlane(flags, mm_to_m(abs(offset)), 0, 0, 0, 0)
            if feat is None:
                raise CADBackendError("FEATURE_FAILED", f"could not create {plane} plane at offset {offset:g} mm")
            self._planes[key] = feat.Name
        return self._planes[key]

    def create_sketch(self, sketch_id: str, plane: str, offset: float) -> None:
        name = self._plane_for(plane, offset)
        self.doc.ClearSelection2(True)
        self._select(name, "PLANE")
        self._sm.InsertSketch(True)
        self._sm.AddToDB = True

    def create_rectangle(self, sketch_id: str, origin: Pt2, length: float, width: float) -> None:
        x0, y0 = origin
        if self._sm.CreateCornerRectangle(mm_to_m(x0), mm_to_m(y0), 0, mm_to_m(x0 + length), mm_to_m(y0 + width), 0) is None:
            raise CADBackendError("SKETCH_FAILED", f"rectangle in {sketch_id} failed")

    def create_circle(self, sketch_id: str, center: Pt2, diameter: float) -> None:
        if self._sm.CreateCircleByRadius(mm_to_m(center[0]), mm_to_m(center[1]), 0, mm_to_m(diameter / 2)) is None:
            raise CADBackendError("SKETCH_FAILED", f"circle in {sketch_id} failed")

    def create_line(self, sketch_id: str, start: Pt2, end: Pt2) -> None:
        if self._sm.CreateLine(mm_to_m(start[0]), mm_to_m(start[1]), 0, mm_to_m(end[0]), mm_to_m(end[1]), 0) is None:
            raise CADBackendError("SKETCH_FAILED", f"line in {sketch_id} failed")

    def create_arc(self, sketch_id: str, center: Pt2, start: Pt2, end: Pt2, direction: str) -> None:
        d = 1 if direction == "ccw" else -1
        if self._sm.CreateArc(mm_to_m(center[0]), mm_to_m(center[1]), 0, mm_to_m(start[0]), mm_to_m(start[1]), 0,
                              mm_to_m(end[0]), mm_to_m(end[1]), 0, d) is None:
            raise CADBackendError("SKETCH_FAILED", f"arc in {sketch_id} failed")

    def create_slot(self, sketch_id: str, center: Pt2, length: float, width: float, angle: float) -> None:
        """Straight slot as two lines + two tangent arcs (verified closed profile)."""
        a = math.radians(angle)
        ux, uy = math.cos(a), math.sin(a)     # along the slot
        nx, ny = -uy, ux                      # normal
        d, r = (length - width) / 2, width / 2
        cx, cy = center
        c1, c2 = (cx - d * ux, cy - d * uy), (cx + d * ux, cy + d * uy)
        p = lambda c, s: (c[0] + s * r * nx, c[1] + s * r * ny)  # noqa: E731
        self.create_line(sketch_id, p(c1, -1), p(c2, -1))
        self.create_arc(sketch_id, c2, p(c2, -1), p(c2, 1), "ccw")
        self.create_line(sketch_id, p(c2, 1), p(c1, 1))
        self.create_arc(sketch_id, c1, p(c1, 1), p(c1, -1), "ccw")

    def close_sketch(self, sketch_id: str) -> None:
        self._sm.AddToDB = False
        self._sm.InsertSketch(True)
        feat = self.doc.FeatureByPositionReverse(0)
        if feat is None or _call(feat, "GetTypeName2") != "ProfileFeature":
            raise CADBackendError("SKETCH_FAILED", f"sketch {sketch_id} was not created")
        self._sketch_names[sketch_id] = feat.Name

    def _select_sketch(self, sketch_id: str) -> None:
        name = self._sketch_names.get(sketch_id)
        if name is None:
            raise CADBackendError("MISSING_DEPENDENCY", f"sketch {sketch_id} does not exist in the model")
        self.doc.ClearSelection2(True)
        self._select(name, "SKETCH")

    # ---- features --------------------------------------------------------------------------------
    def extrude(self, op_id: str, sketch_id: str, distance: float, direction: str, symmetric: bool) -> None:
        self._select_sketch(sketch_id)
        t1 = END_MIDPLANE if symmetric else END_BLIND
        f = self._fm.FeatureExtrusion3(True, False, direction == "reverse", t1, 0, mm_to_m(distance), 0,
                                       False, False, False, False, 0, 0, False, False, False, False,
                                       True, True, True, 0, 0, False)
        if f is None:
            raise CADBackendError("FEATURE_FAILED", f"{op_id}: extrude of {sketch_id} failed")
        self.doc.ClearSelection2(True)

    def cut_extrude(self, op_id: str, sketch_id: str, extent: str, distance: Optional[float], direction: str) -> None:
        self._select_sketch(sketch_id)
        # SolidWorks' default cut direction is opposite the XY-plane normal (i.e. towards -Z, into the
        # material below a sketch plane), so our "reverse" (-Z) is its default and "normal" needs Dir=True.
        rev = direction == "normal"
        if extent == "through_all_both":
            sd, t1, t2, d1 = False, END_THROUGH_ALL, END_THROUGH_ALL, 0.0
        elif extent == "through_all":
            sd, t1, t2, d1 = True, END_THROUGH_ALL, 0, 0.0
        else:
            sd, t1, t2, d1 = True, END_BLIND, 0, mm_to_m(distance or 0.0)
        f = self._fm.FeatureCut4(sd, False, rev, t1, t2, d1, 0, False, False, False, False, 0, 0,
                                 False, False, False, False, False, True, True, True, True, False, 0, 0, False, False)
        if f is None:
            raise CADBackendError("FEATURE_FAILED", f"{op_id}: cut ({extent}) of {sketch_id} failed")
        self.doc.ClearSelection2(True)

    def create_hole(self, op_id: str, diameter: float, position: Pt3, through: bool, depth: Optional[float]) -> None:
        sid = f"{op_id}_sketch"
        x, y, z = position
        self.create_sketch(sid, "XY", 0.0 if through else z)
        self.create_circle(sid, (x, y), diameter)
        self.close_sketch(sid)
        self.cut_extrude(op_id, sid, "through_all_both" if through else "blind", depth, "reverse")

    def _select_edges(self, op_id: str, edge_lines: Sequence[tuple[Pt3, Pt3]], mark: int) -> None:
        """Pick edges by exact end-vertex coordinates from the B-rep (screen picking is unreliable)."""
        targets = [(_m(a), _m(b)) for a, b in edge_lines]
        found = []
        for body in self.doc.GetBodies2(0, False) or []:
            for e in _call(body, "GetEdges") or []:
                v1, v2 = _call(e, "GetStartVertex"), _call(e, "GetEndVertex")
                if v1 is None or v2 is None:
                    continue
                p1, p2 = _call(v1, "GetPoint"), _call(v2, "GetPoint")
                for a, b in targets:
                    if (math.dist(p1, a) < EPS_M and math.dist(p2, b) < EPS_M) or \
                            (math.dist(p1, b) < EPS_M and math.dist(p2, a) < EPS_M):
                        found.append(e)
        if len(found) != len(targets):
            raise CADBackendError("EDGE_NOT_FOUND", f"{op_id}: found {len(found)} of {len(targets)} target edges")
        self.doc.ClearSelection2(True)
        mgr = self.doc.SelectionManager
        for e in found:
            data = _call(mgr, "CreateSelectData")
            data.Mark = mark
            if not e.Select4(True, data):
                raise CADBackendError("SELECTION_FAILED", f"{op_id}: could not select an edge")

    def create_fillet(self, op_id: str, edge_lines: Sequence[tuple[Pt3, Pt3]], radius: float) -> None:
        self._select_edges(op_id, edge_lines, 1)
        f = self._fm.FeatureFillet3(FILLET_OPTIONS, mm_to_m(radius), 0, 0, 0, 0, 0, *(self._empty() for _ in range(7)))
        if f is None:
            raise CADBackendError("FEATURE_FAILED", f"{op_id}: fillet R{radius:g} failed")
        self.doc.ClearSelection2(True)

    def create_chamfer(self, op_id: str, edge_lines: Sequence[tuple[Pt3, Pt3]], distance: float, angle: float) -> None:
        self._select_edges(op_id, edge_lines, 0)
        f = self._fm.InsertFeatureChamfer(4, CHAMFER_ANGLE_DISTANCE, mm_to_m(distance), deg_to_rad(angle), 0, 0, 0, 0)
        if f is None:
            raise CADBackendError("FEATURE_FAILED", f"{op_id}: chamfer {distance:g} x {angle:g}° failed")
        self.doc.ClearSelection2(True)

    # ---- finish ----------------------------------------------------------------------------------
    def rebuild(self) -> None:
        _call(self.doc, "EditRebuild3")

    def inspect(self) -> Optional[dict[str, Any]]:
        bodies = self.doc.GetBodies2(0, False) or []
        if not bodies:
            return {"volume_mm3": 0.0, "bbox": None, "cylinders": [], "bodies": 0}
        mp = _call(self._ext, "CreateMassProperty")
        # GetBodyBox is a loose box around curved faces; extreme points are exact.
        def extreme(dx: float, dy: float, dz: float) -> list[tuple[float, float, float]]:
            out = []
            for b in bodies:
                ok, x, y, z = b.GetExtremePoint(dx, dy, dz, 0.0, 0.0, 0.0)
                out.append((x, y, z))
            return out
        X0 = min(p[0] for p in extreme(-1, 0, 0)); X1 = max(p[0] for p in extreme(1, 0, 0))
        Y0 = min(p[1] for p in extreme(0, -1, 0)); Y1 = max(p[1] for p in extreme(0, 1, 0))
        Z0 = min(p[2] for p in extreme(0, 0, -1)); Z1 = max(p[2] for p in extreme(0, 0, 1))
        k = 1000.0  # m -> mm, reporting only
        bbox = [X0 * k, -Z1 * k, Y0 * k, X1 * k, -Z0 * k, Y1 * k]
        cylinders = []
        for b in bodies:
            for face in _call(b, "GetFaces") or []:
                s = _call(face, "GetSurface")
                if _call(s, "IsCylinder"):
                    p = s.CylinderParams
                    X, Y, Z, r = p[0] * k, p[1] * k, p[2] * k, p[6] * k
                    if abs(abs(p[4]) - 1.0) < 1e-6:    # model Y == part Z: centre in part (x, y)
                        cylinders.append({"axis": "z", "center": [X, -Z], "radius": r})
                    elif abs(abs(p[3]) - 1.0) < 1e-6:  # model X == part X: centre in part (y, z)
                        cylinders.append({"axis": "x", "center": [-Z, Y], "radius": r})
                    elif abs(abs(p[5]) - 1.0) < 1e-6:  # model Z == part -Y: centre in part (x, z)
                        cylinders.append({"axis": "y", "center": [X, Y], "radius": r})
        try:
            rebuild_errors = int(_call(self._ext, "GetWhatsWrongCount"))
        except Exception:  # noqa: BLE001 - informational only
            rebuild_errors = None
        return {"volume_mm3": mp.Volume * 1e9, "bbox": bbox, "cylinders": cylinders, "bodies": len(bodies),
                "rebuild_errors": rebuild_errors}

    def _save_as(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        err = self.doc.SaveAs3(str(path.resolve()), 0, SAVE_SILENT)
        if err != 0 or not path.exists():
            raise CADBackendError("SAVE_FAILED", f"SolidWorks could not write {path} (error {err})")

    def save(self, path: Path) -> None:
        self._save_as(path)

    def export_step(self, path: Path) -> None:
        self._save_as(path)

    def export_stl(self, path: Path) -> None:
        """Binary, fine, and NOT translated to positive space so the STL keeps model coordinates.
        These are application-wide options, so the user's previous values are restored."""
        toggles = {STL_DONT_TRANSLATE: True, STL_BINARY: True}
        old_t = {k: self.sw.GetUserPreferenceToggle(k) for k in toggles}
        old_q = self.sw.GetUserPreferenceIntegerValue(STL_QUALITY)
        try:
            for k, v in toggles.items():
                self.sw.SetUserPreferenceToggle(k, v)
            self.sw.SetUserPreferenceIntegerValue(STL_QUALITY, STL_QUALITY_FINE)
            self._save_as(path)
        finally:
            for k, v in old_t.items():
                self.sw.SetUserPreferenceToggle(k, v)
            self.sw.SetUserPreferenceIntegerValue(STL_QUALITY, old_q)

    def close(self) -> None:
        if self.sw is not None and self.doc is not None:
            self.sw.CloseDoc(_call(self.doc, "GetTitle"))
        self.doc = None
