"""CLI entry point:  python -m src.main --input input/drawing.png"""
from __future__ import annotations

import argparse
import sys

from . import extractor, utils
from .vlm_client import create_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 1: 2D engineering drawing -> geometry JSON")
    parser.add_argument("--input", required=True, help="Drawing image (PNG/JPG/JPEG/WEBP)")
    parser.add_argument("--output-dir", default="output", help="Output root (default: output)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the second (verification) VLM pass: faster, less reliable")
    args = parser.parse_args(argv)

    try:
        image = utils.validate_input_path(args.input)  # fail fast before needing credentials
        client = create_client()
        report = extractor.run(image, client, args.output_dir, verify=not args.no_verify)
    except utils.Stage1Error as exc:
        utils.error(str(exc))
        return 1
    except KeyboardInterrupt:
        utils.error("Interrupted.")
        return 130

    if report.extraction_status == "failed":
        for e in report.errors:
            utils.error(e)
        return 1
    if report.extraction_status == "partial":
        utils.warning("Extraction is PARTIAL: review missing_information / ambiguities in geometry.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
