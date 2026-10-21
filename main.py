"""Pipeline CLI (stages 2-5; Stage 1 via --image).

  python main.py --image input/part.png                       # stages 1-5
  python main.py --geometry output/part/geometry.json --stage validate
  python main.py --geometry output/part/geometry.json --stage plan
  python main.py --geometry output/part/geometry.json --stage cad [--dry-run]
  --allow-review : DEVELOPMENT OVERRIDE, build REVIEW_REQUIRED geometry (never FAIL)
  python -m webapp                                            # web front end

Outputs go to the geometry's folder (or --output-dir); see README.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from orchestrator import OUTPUT, Run
from utils.logging_config import setup_console

log = logging.getLogger("main")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="2D drawing -> validated geometry -> SolidWorks model")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path, help="drawing image: run all stages starting with Stage 1")
    src.add_argument("--geometry", type=Path, help="Stage 1 geometry.json: start at Stage 2")
    p.add_argument("--stage", choices=["validate", "plan", "cad"], default="cad")
    p.add_argument("--dry-run", action="store_true", help="simulate CAD without opening SolidWorks")
    p.add_argument("--allow-review", action="store_true", help="DEVELOPMENT OVERRIDE for REVIEW_REQUIRED")
    p.add_argument("--output-dir", type=Path)
    args = p.parse_args(argv)
    setup_console()

    if args.image:
        if not args.image.is_file():
            log.error("image not found: %s", args.image)
            return 1
        run = Run(args.output_dir or OUTPUT / args.image.stem)
        state = run.run(image=args.image, allow_review=args.allow_review, dry_run=args.dry_run)
    else:
        if not args.geometry.is_file():
            log.error("geometry file not found: %s", args.geometry)
            return 1
        out_dir = args.output_dir or args.geometry.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        if args.geometry.resolve() != (out_dir / "geometry.json").resolve() and args.geometry.name == "geometry.json":
            shutil.copy(args.geometry, out_dir / "geometry.json")
        run = Run(out_dir)
        if args.stage == "validate":
            return 0 if run.stage2()["status"] == "PASS" else 2
        if args.stage == "plan":
            validated = run.stage2()
            if validated["status"] == "FAIL" or (validated["status"] != "PASS" and not args.allow_review):
                log.error("Validation is %s: planning refused (see validation_report.json).", validated["status"])
                return 2
            return 0 if run.stage3(validated, args.allow_review) else 2
        state = run.run(start="stage2", allow_review=args.allow_review, dry_run=args.dry_run)

    final = [s for s in ("stage1", "stage2", "stage3", "stage4", "stage5") if s in state["stages"]]
    for s in final:
        log.info("%s: %s  %s", s, state["stages"][s]["status"], state["stages"][s]["message"])
    last = state["stages"][final[-1]]["status"] if final else "FAIL"
    return 0 if last == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
