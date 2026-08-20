"""Measurement index upsert / rebuild."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import measurement_index_path, sessions_root
from .facts import load_json
from .io import write_text


def load_index(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or measurement_index_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def upsert_index_entry(entry: dict[str, Any], path: Path | None = None) -> Path:
    path = path or measurement_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_index(path)
    sid = entry.get("session_id")
    kept = [r for r in existing if r.get("session_id") != sid]
    kept.append(entry)
    text = "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in kept)
    write_text(path, text)
    return path


def rebuild_measurement_index(
    root: Path | None = None, path: Path | None = None
) -> dict[str, Any]:
    root = root or sessions_root()
    path = path or measurement_index_path()
    entries = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        m = load_json(d / "evaluation" / "review-measurement.json")
        if not isinstance(m, dict):
            continue
        cand = load_json(d / "evaluation" / "eval-candidate.json") or {}
        entries.append(
            {
                "schema_version": 1,
                "session_id": m.get("session_id") or d.name,
                "packet_hash": m.get("packet_hash"),
                "completed_at": m.get("captured_at"),
                "review_type": m.get("review_type"),
                "adjudication_state": m.get("adjudication_state"),
                "eval_candidate": bool(cand) and cand.get("promoted") is False,
                "flags": m.get("flags") or [],
                "session_path": str(d),
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in entries)
    write_text(path, text)
    return {"ok": True, "count": len(entries), "path": str(path)}
