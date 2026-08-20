"""Atomic write helpers for evaluation artefacts."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_multi_write(target_dir: Path, files: dict[str, str | bytes]) -> None:
    """Write multiple files via tempdir + rename into target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="yonko-eval-", dir=str(target_dir.parent)))
    try:
        staging = tmp_root / "staging"
        staging.mkdir()
        for rel, content in files.items():
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                dest.write_bytes(content)
            else:
                dest.write_text(content, encoding="utf-8")
        for rel in files:
            src = staging / rel
            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
