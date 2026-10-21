"""Tellis settings, read from the repository .env (never log secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
OUTPUT = PKG / "output"
STATIC = PKG / "static"


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    trellis_provider: str       # huggingface | remote | local | none
    trellis_url: str            # remote TRELLIS.2 server (tellis/trellis_server.py on a GPU box)
    trellis_model: str          # HF model id for the local provider
    trellis_timeout: float
    hf_space: str               # Hugging Face Space for the huggingface provider
    hf_token: str               # optional; raises the ZeroGPU quota
    default_size_mm: float      # longest side when the user gives no real-world size
    max_faces: int              # triangle budget for the SolidWorks import (one B-rep face per triangle)
    solidworks_visible: bool


def load() -> Settings:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.getenv("TRELLIS_URL", "").strip().rstrip("/")
    provider = os.getenv("TRELLIS_PROVIDER", "").strip().lower()
    if not provider:
        provider = "remote" if url else ("local" if _trellis_installed() else "huggingface")
    return Settings(
        trellis_provider=provider,
        trellis_url=url,
        trellis_model=os.getenv("TRELLIS_MODEL", "microsoft/TRELLIS.2-4B").strip(),
        trellis_timeout=_float("TRELLIS_TIMEOUT_SECONDS", 900),
        hf_space=os.getenv("TRELLIS_HF_SPACE", "").strip() or "microsoft/TRELLIS.2",
        hf_token=os.getenv("HF_TOKEN", "").strip(),
        default_size_mm=_float("TELLIS_DEFAULT_SIZE_MM", 100),
        max_faces=int(_float("TELLIS_MAX_FACES", 20000)),
        solidworks_visible=os.getenv("SW_VISIBLE", "true").strip().lower() in ("1", "true", "yes"),
    )


def _trellis_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("trellis2") is not None
