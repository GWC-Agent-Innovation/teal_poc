"""Shared helpers: logging, errors, file IO, JSON cleanup."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class Stage1Error(Exception):
    """Base error carrying a user-friendly message."""


class InputError(Stage1Error):
    pass


class VLMError(Stage1Error):
    pass


class VLMAuthError(VLMError):
    pass


class VLMTimeoutError(VLMError):
    pass


class EmptyResponseError(VLMError):
    pass


class MalformedJSONError(Stage1Error):
    pass


class OutputError(Stage1Error):
    pass


def _log(level: str, msg: str) -> None:
    stream = sys.stderr if level in ("WARNING", "ERROR") else sys.stdout
    print(f"[{level}] {msg}", file=stream, flush=True)


def info(msg: str) -> None:
    _log("INFO", msg)


def warning(msg: str) -> None:
    _log("WARNING", msg)


def error(msg: str) -> None:
    _log("ERROR", msg)


def validate_input_path(path: str | Path) -> Path:
    p = Path(path)
    if not p.exists():
        raise InputError(f"Input image not found: {p}")
    if not p.is_file():
        raise InputError(f"Input path is not a file: {p}")
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InputError(
            f"Unsupported image format '{p.suffix}'. Supported: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )
    return p


_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence, if any."""
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text.strip()


def parse_json_response(text: str) -> dict:
    """Parse VLM text into a JSON object; tolerate fences and surrounding prose."""
    if not text or not text.strip():
        raise EmptyResponseError("The VLM returned an empty response.")
    cleaned = strip_code_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise MalformedJSONError("VLM response does not contain a JSON object.")
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise MalformedJSONError(f"VLM response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedJSONError("VLM response JSON must be an object at the top level.")
    return data


def write_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OutputError(f"Could not write {path}: {exc}") from exc


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
