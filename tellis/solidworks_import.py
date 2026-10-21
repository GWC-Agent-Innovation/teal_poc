"""SolidWorks step: import the prepared mesh as a solid body, save SLDPRT and export STEP.

Reuses the repo's SolidWorks COM backend (connection, inspection, saving); only the STL import is new."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from stage3.cad.backend import CADBackendError
from stage3.cad.backends.solidworks_backend import SolidWorksBackend, _call

log = logging.getLogger("tellis.solidworks")

# swconst.tlb
IMPORT_MODEL_TYPE, IMPORT_UNITS = 208, 210        # swUserPreferenceIntegerValue_e
MODEL_TYPE_SOLID, UNITS_MM = 2, 0                 # swImportStlVrmlModelType_Solid, swLengthUnit_e.swMM
IMPORT_AS_PS_MESH = 679                           # swVrmlStlImportAsPSMesh (mesh B-rep) -> off: classic solid
SOLID_BODY, SHEET_BODY = 0, 1                     # swBodyType_e


class SolidWorksMeshImporter(SolidWorksBackend):
    name = "solidworks_mesh_import"

    def import_mesh(self, stl: Path) -> dict[str, Any]:
        self._connect()
        ints = {IMPORT_MODEL_TYPE: MODEL_TYPE_SOLID, IMPORT_UNITS: UNITS_MM}
        old_i = {k: self.sw.GetUserPreferenceIntegerValue(k) for k in ints}
        old_t = self.sw.GetUserPreferenceToggle(IMPORT_AS_PS_MESH)
        try:  # application-wide options: set for this import only, then restore the user's values
            for k, v in ints.items():
                self.sw.SetUserPreferenceIntegerValue(k, v)
            self.sw.SetUserPreferenceToggle(IMPORT_AS_PS_MESH, False)
            err = self._win32.VARIANT(self._pythoncom.VT_BYREF | self._pythoncom.VT_I4, 0)
            self.doc = self.sw.LoadFile4(str(stl.resolve()), "r", self._none, err)
        finally:
            for k, v in old_i.items():
                self.sw.SetUserPreferenceIntegerValue(k, v)
            self.sw.SetUserPreferenceToggle(IMPORT_AS_PS_MESH, old_t)
        if self.doc is None:
            raise CADBackendError("IMPORT_FAILED", f"SolidWorks could not import {stl.name} (error {err.value})")
        self._ext = self.doc.Extension
        solids = list(self.doc.GetBodies2(SOLID_BODY, False) or [])
        sheets = list(self.doc.GetBodies2(SHEET_BODY, False) or [])
        if not solids and not sheets:
            raise CADBackendError("IMPORT_FAILED", "import produced no body")
        # A mesh solid has one face per triangle, so no per-face COM calls here (inspect() would take minutes).
        info: dict[str, Any] = {"solid_bodies": len(solids), "surface_bodies": len(sheets),
                                "faces": sum(int(_call(b, "GetFaceCount")) for b in solids + sheets)}
        if solids:
            info["volume_mm3"] = round(_call(self._ext, "CreateMassProperty").Volume * 1e9, 1)
        x0, y0, z0, x1, y1, z1 = (v * 1000.0 for v in _call((solids or sheets)[0], "GetBodyBox"))
        info["bbox"] = [round(v, 3) for v in (x0, -z1, y0, x1, -z0, y1)]   # SolidWorks frame -> part frame, mm
        return info


def run(stl_sw_frame: Path, out_dir: Path, visible: bool = True) -> dict[str, Any]:
    """Import, save and export. Returns a cad_result dict (status PASS/FAIL, files, measured)."""
    files: dict[str, str] = {}
    sw = None
    try:
        sw = SolidWorksMeshImporter(visible=visible)
        measured = sw.import_mesh(stl_sw_frame)
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, name, fn in (("sldprt", "part.SLDPRT", sw.save), ("step", "part.STEP", sw.export_step),
                              ("stl", "part.STL", sw.export_stl)):
            fn(out_dir / name)
            files[key] = str(out_dir / name)
            log.info("Saved: %s", out_dir / name)
        warnings = [] if measured["solid_bodies"] else ["imported as a surface body (mesh was not closed)"]
        return {"status": "PASS", "measured": measured, "files": files, "warnings": warnings, "errors": []}
    except CADBackendError as exc:
        log.error("SolidWorks: [%s] %s", exc.code, exc.message)
        return {"status": "FAIL", "files": files, "errors": [{"code": exc.code, "message": exc.message}]}
    finally:
        if sw is not None:
            try:
                sw.close()
            except Exception as exc:  # noqa: BLE001 - closing must not mask the real result
                log.warning("SolidWorks close failed: %s", exc)
