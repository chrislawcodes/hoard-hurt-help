#!/usr/bin/env python3
"""Text I/O helpers with optional command-telemetry accounting."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
import sys

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_telemetry_commands import current_ctx  # noqa: E402


def _note_read(text: str) -> None:
    ctx = current_ctx()
    if ctx is None:
        return
    ctx.files_read += 1
    ctx.input_bytes_read += len(text.encode("utf-8"))


def _note_write(text: str) -> None:
    ctx = current_ctx()
    if ctx is None:
        return
    ctx.files_written += 1
    ctx.output_bytes_written += len(text.encode("utf-8"))


def read_text(path: Path | str, encoding: str = "utf-8") -> str:
    text = Path(path).read_text(encoding=encoding)
    _note_read(text)
    return text


def write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding=encoding)
    _note_write(text)


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp.", suffix=target.suffix or ".txt")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        tmp_path.replace(target)
        _note_write(text)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
