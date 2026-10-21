"""Logging setup: console + per-run log files. Never log secrets."""
from __future__ import annotations

import logging
from pathlib import Path

_FMT = "[%(levelname)s] %(name)s: %(message)s"


def setup_console(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(_FMT))
        root.addHandler(h)
    root.setLevel(level)


def add_file_log(logger_names: list[str], path: Path) -> logging.FileHandler:
    """Attach a file handler to the given loggers; caller should remove it when done."""
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(path, mode="w", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s " + _FMT))
    for name in logger_names:
        logging.getLogger(name).addHandler(h)
    return h


def remove_file_log(logger_names: list[str], handler: logging.FileHandler) -> None:
    for name in logger_names:
        logging.getLogger(name).removeHandler(handler)
    handler.close()
