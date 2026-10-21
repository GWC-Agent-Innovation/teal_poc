"""Stage 1 pipeline: image -> VLM -> validated geometry -> output/<name>/."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from . import utils, validator
from .schemas import ExtractionReport, Geometry
from .utils import Stage1Error, info, warning

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
EXTRACTION_PROMPT = "engineering_extraction_prompt.txt"
VERIFICATION_PROMPT = "verification_prompt.txt"


def _prepare_drawing(image: Path, out_dir: Path) -> Path:
    """Build a reviewable image sheet with the original and enlarged overlapping crops.

    Engineering sheets often contain legible geometry but very small callouts when
    sent as one image. The crop panels preserve local text/detail while the original
    panel keeps the drawing's view layout and cross-view context.
    """
    try:
        from PIL import Image, ImageDraw, ImageOps

        with Image.open(image) as source:
            original = ImageOps.exif_transpose(source).convert("RGB")
        width, height = original.size
        crop_w, crop_h = max(1, int(width * 0.58)), max(1, int(height * 0.58))
        starts_x = [0, max(0, width - crop_w)]
        starts_y = [0, max(0, height - crop_h)]
        panel_w = min(1200, max(640, width))
        scale = panel_w / crop_w
        panel_h = int(crop_h * scale)
        margin, label_h = 24, 36
        original_w = min(1800, max(900, width))
        original_h = int(height * original_w / width)
        canvas_w = margin * 3 + panel_w * 2
        canvas_h = margin * 4 + label_h * 2 + original_h + panel_h * 2 + 24
        sheet = Image.new("RGB", (canvas_w, canvas_h), "white")
        draw = ImageDraw.Draw(sheet)
        draw.text((margin, margin), "FULL DRAWING - layout and cross-view context", fill="black")
        y = margin + label_h
        sheet.paste(original.resize((original_w, original_h), Image.Resampling.LANCZOS), (margin, y))
        y += original_h + margin
        for row, top in enumerate(starts_y):
            for col, left in enumerate(starts_x):
                x = margin + col * (panel_w + margin)
                panel_y = y + row * (panel_h + label_h + margin)
                draw.text((x, panel_y), f"DETAIL CROP {row * 2 + col + 1} - enlarged", fill="black")
                crop = original.crop((left, top, left + crop_w, top + crop_h))
                sheet.paste(crop.resize((panel_w, panel_h), Image.Resampling.LANCZOS),
                            (x, panel_y + label_h))
        prepared = out_dir / "drawing_vlm_input.jpg"
        sheet.save(prepared, "JPEG", quality=94, optimize=True)
        return prepared
    except Exception as exc:
        warning(f"Could not prepare enlarged drawing crops ({exc}); using original image.")
        return image


def load_prompt(name: str = EXTRACTION_PROMPT) -> str:
    path = PROMPTS_DIR / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Stage1Error(f"Could not read prompt file {path}: {exc}") from exc
    if not text.strip():
        raise Stage1Error(f"Prompt file is empty: {path}")
    return text


def _ask_json(client, image: Path, prompt: str, raw_log: dict, key: str) -> dict:
    """Call the VLM and parse JSON; on malformed JSON retry once (long outputs occasionally break)."""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        raw = client.extract(image, prompt)
        raw_log[key if attempt == 1 else f"{key}_retry"] = raw
        try:
            return utils.parse_json_response(raw)
        except utils.MalformedJSONError as exc:
            last_exc = exc
            if attempt == 1:
                warning(f"Malformed JSON from VLM ({exc}); retrying once...")
    raise last_exc


def _verify(client, image: Path, data: dict, raw_log: dict) -> tuple[dict, list[str]]:
    """Second VLM pass: a checker traces every dimension against the drawing.

    Falls back to the first-pass result if the checker fails, so verification can
    only improve the output, never lose it.
    """
    prompt = load_prompt(VERIFICATION_PROMPT).replace(
        "{{EXTRACTION_JSON}}", json.dumps(data, indent=1, ensure_ascii=False))
    info("Verifying extraction against the drawing (pass 2)...")
    try:
        checked = _ask_json(client, image, prompt, raw_log, "verification_raw_text")
    except (utils.VLMError, utils.MalformedJSONError) as exc:
        warning(f"Verification pass failed ({exc}); using first-pass extraction.")
        return data, [f"verification pass failed: {exc}"]
    if not checked.get("features"):
        warning("Verification pass returned no features; using first-pass extraction.")
        return data, ["verification pass returned no features; first pass kept"]
    log = checked.pop("review_log", []) or []
    log = [str(x) for x in log] if isinstance(log, list) else [str(log)]
    info(f"Verification made {len(log)} change(s).")
    for entry in log:
        info(f"  review: {entry}")
    return checked, log


def _count_missing_required(geometry: Geometry) -> int:
    return sum(1 for m in geometry.missing_information if m.required_for_cad)


def _decide_status(geometry: Geometry) -> str:
    if not geometry.features:
        return "failed"
    if _count_missing_required(geometry) or geometry.ambiguities:
        return "partial"
    return "success"


def run(input_path: str | Path, client, output_root: str | Path = "output",
        verify: bool = True) -> ExtractionReport:
    """Run Stage 1. Always writes an extraction_report.json once output dir exists."""
    image = utils.validate_input_path(input_path)
    out_dir = Path(output_root) / image.stem
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = ExtractionReport(input_file=image.name, extraction_status="failed",
                              model=getattr(client, "model", None), extracted_at=now)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise utils.OutputError(f"Could not create output directory {out_dir}: {exc}") from exc

    def finish(err: Exception | None = None) -> ExtractionReport:
        if err is not None:
            report.errors.append(str(err))
        utils.write_json(out_dir / "extraction_report.json", report.model_dump())
        info(f"Saved: {out_dir / 'extraction_report.json'}")
        return report

    try:
        info(f"Loading drawing: {image}")
        vlm_image = _prepare_drawing(image, out_dir)
        prompt = load_prompt()
        info("Sending drawing to VLM...")
        raw_log: dict = {"model": report.model}
        raw_path = out_dir / "raw_vlm_response.json"
        try:
            data = _ask_json(client, vlm_image, prompt, raw_log, "raw_text")
        finally:
            # Keep the raw response(s) for debugging regardless of what follows.
            utils.write_json(raw_path, raw_log)
            info(f"Saved: {raw_path}")
        info("VLM response received.")
        review_log: list[str] = []
        if verify:
            data, review_log = _verify(client, vlm_image, data, raw_log)
            utils.write_json(raw_path, raw_log)
        data["review_log"] = review_log
        info("Auditing values against their evidence...")
        flags = validator.audit(data)
        for f in flags:
            warning(f"Audit: {f}")
        data["validation_flags"] = flags
        info("Validating geometry schema...")
        try:
            geometry = Geometry.model_validate(data)
        except ValidationError as exc:
            for e in exc.errors()[:10]:
                utils.error(f"Schema problem at {'.'.join(map(str, e['loc']))}: {e['msg']}")
            raise Stage1Error(f"Geometry failed schema validation ({exc.error_count()} problem(s)). "
                              "See raw_vlm_response.json.") from exc

        geometry.extraction_metadata.source_file = image.name
        geometry.extraction_metadata.model = report.model
        geometry.extraction_metadata.extracted_at = now

        report.features_detected = len(geometry.features)
        report.missing_required_values = _count_missing_required(geometry)
        report.ambiguities = len(geometry.ambiguities)
        report.rejected_values = sum(1 for f in flags if "null value was not listed" not in f)
        report.validation_flags = len(flags)
        report.verification_changes = len(review_log)
        report.extraction_status = _decide_status(geometry)
        # The reported status is ours, not the model's; keep them consistent.
        geometry.extraction_metadata.status = (
            "complete" if report.extraction_status == "success" else report.extraction_status
        )

        info(f"Features detected: {report.features_detected}")
        if report.missing_required_values:
            warning(f"Missing dimensions: {report.missing_required_values}")
        if report.ambiguities:
            warning(f"Ambiguities: {report.ambiguities}")
        if report.extraction_status == "failed":
            warning("No features were extracted; geometry.json not written.")
            return finish(Stage1Error("VLM returned no features."))

        utils.write_json(out_dir / "geometry.json", geometry.model_dump(mode="json"))
        info(f"Saved: {out_dir / 'geometry.json'}")
        return finish()
    except Stage1Error as exc:
        report.extraction_status = "failed"
        return finish(exc)
