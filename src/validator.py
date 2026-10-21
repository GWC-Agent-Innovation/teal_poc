"""Deterministic post-checks on the VLM output (runs before schema validation).

The VLM is told not to guess, but it does not always comply. These checks are
drawing-independent and enforce the Stage 1 rules in code:

1. Every numeric value that carries evidence must appear literally in that
   evidence's printed text. Otherwise it was derived (summed, halved, assumed)
   and is rejected: value -> null, original kept as `rejected_value`.
2. Values with no evidence, or whose evidence describes reasoning instead of
   quoting the drawing ("symmetric appearance", "assumed", ...), are rejected likewise.
3. Every null / missing value is guaranteed to be listed in `missing_information`.
4. A dimension's stated orientation must match its reference edge
   (horizontal <-> left/right, vertical <-> top/bottom); conflicts are rejected.
5. Positioning dimensions nested inside features are also mirrored into the
   top-level `relationships` list, so Stage 2 has a single place to read them.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Iterator

# Words that indicate the model reasoned instead of quoting printed text.
_INFERENCE_RE = re.compile(
    r"\b(appear\w*|visual\w*|assum\w*|impl(?:y|ied|ies)|infer\w*|estimat\w*|approx\w*|looks?|"
    r"seems?|likely|probabl\w*|deriv\w*|calculat\w*|comput\w*|sum(?:med)?|total(?:led)?|"
    r"symmetric(?:al)?\s+(?:appearance|layout|placement)|by\s+symmetry|measured|proportion\w*|pixel\w*)\b",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"(\d+\s*/\s*\d+|\d*\.\d+|\d+)")
# Keys whose numeric value is NOT an engineering value needing evidence.
_NON_VALUE_KEYS = {"confidence", "quantity", "instance", "sheet"}


def _numbers_in(text: str) -> list[float]:
    out = []
    for tok in _NUM_RE.findall(text or ""):
        tok = tok.replace(" ", "")
        try:
            out.append(float(Fraction(tok)) if "/" in tok else float(tok))
        except (ValueError, ZeroDivisionError):
            pass
    return out


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _walk(obj: Any, path: str) -> Iterator[tuple[str, dict]]:
    """Yield (path, dict) for every dict; feature entries are named by their id."""
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            name = v.get("id") or v.get("feature_id") if isinstance(v, dict) else None
            yield from _walk(v, f"{path}[{name}]" if name else f"{path}[{i}]")


def _flatten_relationship_values(data: dict) -> None:
    """Models sometimes nest a whole Quantity in a relationship's `value`; flatten it."""
    for _, d in _walk(data, ""):
        v = d.get("value")
        if isinstance(v, dict) and ("value" in v or "evidence" in v) and "type" in d:
            for key in ("unit", "evidence", "status", "tolerance", "confidence"):
                if key in v and d.get(key) is None:
                    d[key] = v[key]
            d["value"] = v.get("value")


def _mirror_positioning(data: dict) -> None:
    rels = data.setdefault("relationships", [])
    if not isinstance(rels, list):
        return
    existing = {r.get("feature_id") for r in rels if isinstance(r, dict)}
    for f in data.get("features") or []:
        if not isinstance(f, dict):
            continue
        dims = (f.get("positioning") or {}).get("dimensions") if isinstance(f.get("positioning"), dict) else None
        if dims and f.get("id") not in existing:
            rels.append({"feature_id": f.get("id"), "relationships": [d for d in dims if isinstance(d, dict)]})


_VERTICAL_REFS = re.compile(r"(?<![a-z])(top|bottom|upper|lower)(?![a-z])", re.IGNORECASE)
_HORIZONTAL_REFS = re.compile(r"(?<![a-z])(left|right)(?![a-z])", re.IGNORECASE)


def _orientation_conflict(d: dict) -> str | None:
    """A horizontal dimension cannot measure from a top/bottom edge (and vice versa)."""
    orient = str(d.get("orientation") or "").lower()
    ref = str(d.get("reference") or "")
    if "edge" not in ref.lower():
        return None
    if orient == "horizontal" and _VERTICAL_REFS.search(ref) and not _HORIZONTAL_REFS.search(ref):
        return f"horizontal dimension cannot measure from '{ref}'"
    if orient == "vertical" and _HORIZONTAL_REFS.search(ref) and not _VERTICAL_REFS.search(ref):
        return f"vertical dimension cannot measure from '{ref}'"
    return None


def audit(data: dict) -> list[str]:
    """Mutate `data` in place to enforce the rules; return human-readable flags."""
    flags: list[str] = []
    _flatten_relationship_values(data)
    _mirror_positioning(data)
    missing = data.setdefault("missing_information", [])
    if not isinstance(missing, list):
        missing = data["missing_information"] = []

    seen_rejections: set[tuple[str, float]] = set()
    for path, d in _walk(data, ""):
        if path.startswith(("missing_information", "ambiguities", "extraction_metadata")):
            continue
        v = d.get("value")
        ev = d.get("evidence")
        if not _is_num(v) or path.rsplit(".", 1)[-1] in _NON_VALUE_KEYS:
            continue
        raw = ev.get("raw_text") if isinstance(ev, dict) else ev if isinstance(ev, str) else None
        if raw is None and isinstance(d.get("raw_text"), str):
            raw = d["raw_text"]  # e.g. occurrences entries carry raw_text inline
        if not raw:
            reason = "value has no printed evidence (raw_text is empty)"
        elif _INFERENCE_RE.search(raw):
            reason = f"evidence describes an inference, not printed text: '{raw}'"
        elif not any(abs(float(v) - n) <= 1e-6 * max(1.0, abs(n)) for n in _numbers_in(raw)):
            reason = f"value {v} does not appear in its evidence text '{raw}' (derived, not printed)"
        else:
            reason = _orientation_conflict(d)
        if reason:
            d["rejected_value"], d["rejection_reason"] = v, reason
            d["value"] = None
            if "status" in d or "unit" in d:
                d["status"] = "rejected"
            # The same sub-object can be reachable twice (positioning mirrored into relationships).
            key = (path.split(".")[0] + reason, float(v))
            if key not in seen_rejections:
                seen_rejections.add(key)
                flags.append(f"{path}: {reason}")
                missing.append({"field": path, "reason": f"Rejected unsupported value: {reason}",
                                "required_for_cad": True})

    # Every null quantity inside a feature / overall dimension must be listed as missing.
    listed = " ".join(str(m.get("field", "")) for m in missing if isinstance(m, dict))
    for path, d in _walk({"features": data.get("features"), "overall_dimensions": data.get("overall_dimensions")}, ""):
        if "value" in d and d.get("value") is None and d.get("status") != "rejected" and ".dimensions." in f".{path}":
            if path not in listed and path.replace("[", ".").replace("]", "") not in listed:
                missing.append({"field": path, "reason": "Value not found in the drawing", "required_for_cad": True})
                flags.append(f"{path}: null value was not listed in missing_information (added)")

    return flags
