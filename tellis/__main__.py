"""Tellis CLI (run from the repository root).

    python -m tellis serve [--port 8100]
    python -m tellis run --image drawing.png [--size-mm 120] [--max-faces 20000] [--mesh existing.glb] [--dry-run]
    python -m tellis drawing --image drawing.jpg [--thickness 10] [--relief 3]      (exact part from a drawing)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from utils.logging_config import setup_console


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tellis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the backend + frontend")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8100)
    r = sub.add_parser("run", help="run one image through the pipeline")
    r.add_argument("--image", required=True, type=Path)
    r.add_argument("--size-mm", type=float, help="real length of the object's longest side")
    r.add_argument("--max-faces", type=int, help="triangle budget for the SolidWorks import (default 20000)")
    r.add_argument("--mesh", type=Path, help="use this mesh instead of calling TRELLIS.2")
    r.add_argument("--dry-run", action="store_true", help="stop before SolidWorks")
    d = sub.add_parser("drawing", help="dimensioned 2D drawing -> exact SolidWorks part (trace + VLM dimensions)")
    d.add_argument("--image", required=True, type=Path)
    d.add_argument("--thickness", type=float, default=10.0, help="plate thickness, mm (not on a single view)")
    d.add_argument("--relief", type=float, default=3.0, help="raise the main inner panel by this much, mm (0 = off)")
    d.add_argument("--height-mm", type=float, help="overall height, if the VLM cannot read it")
    d.add_argument("--out", type=Path, help="output folder (default tellis/output/drawing-<image name>)")
    d.add_argument("--no-solidworks", action="store_true", help="stop after cad_plan.json")
    d.add_argument("--reread", action="store_true", help="ask the VLM again even if dimensions.json exists")
    a = ap.parse_args(argv)
    setup_console()

    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("tellis.server:app", host=a.host, port=a.port)
        return 0

    if a.cmd == "drawing":
        from .config import OUTPUT

        if not a.image.is_file():
            print(f"image not found: {a.image}\n(paths with spaces or brackets need quotes, e.g. "
                  f'--image "C:\\folder\\Media (6).jpg")', file=sys.stderr)
            return 1
        from .drawing.pipeline import run

        out = run(a.image, a.out or OUTPUT / f"drawing-{a.image.stem}", a.thickness, a.relief, a.height_mm,
                  solidworks=not a.no_solidworks, reuse_dimensions=not a.reread)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["status"] in ("PASS", "SKIPPED") else 2

    from .pipeline import Job

    if not a.image.is_file():
        print(f"image not found: {a.image}", file=sys.stderr)
        return 1
    job = Job.create(a.image, size_mm=a.size_mm, dry_run=a.dry_run, mesh=a.mesh, max_faces=a.max_faces)
    job.run()
    summary = job.summary()
    print(json.dumps({"job": job.id, "dir": str(job.dir), "status": summary["status"],
                      "stages": {k: f"{v['status']}: {v.get('message', '')}" for k, v in summary["stages"].items()},
                      "files": sorted(summary["files"])}, indent=2))
    return 0 if summary["status"] == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
