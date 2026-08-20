"""Session fact extraction for evaluation capture (no ledger import)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

SEATS = ("shanks", "blackbeard", "buggy", "luffy")


def load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def norm_title(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t[:160]


def finding_fingerprint(f: dict[str, Any]) -> str:
    title = norm_title(str(f.get("title") or f.get("id") or ""))
    cat = str(f.get("category") or "").lower()
    locus = f.get("locus") if isinstance(f.get("locus"), dict) else {}
    path = str(locus.get("path") or "")
    return f"{cat}|{path}|{title}"


def iter_findings_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("findings", "plan_findings", "document_findings", "accepted", "held"):
        arr = data.get(key)
        if isinstance(arr, list):
            return [x for x in arr if isinstance(x, dict)]
    return []


def bucket_findings(session_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "accepted": [],
        "dropped": [],
        "held": [],
        "seat_emitted": [],
        "merged": [],
        "form": "missing",
        "notes_count": 0,
    }
    merged = load_json(session_dir / "findings.json")
    if merged is None:
        out["form"] = "missing"
    elif isinstance(merged, dict) and any(
        k in merged for k in ("accepted", "dropped", "held", "notes")
    ):
        out["form"] = "adjudication_buckets"
        for key in ("accepted", "dropped", "held"):
            arr = merged.get(key)
            if isinstance(arr, list):
                out[key] = [x for x in arr if isinstance(x, dict)]
        notes = merged.get("notes")
        if isinstance(notes, list):
            out["notes_count"] = len([x for x in notes if isinstance(x, dict)])
    elif isinstance(merged, dict) and isinstance(merged.get("plan_findings"), list):
        out["form"] = "plan_array_form"
        out["merged"] = [x for x in merged["plan_findings"] if isinstance(x, dict)]
    elif isinstance(merged, dict) and isinstance(merged.get("document_findings"), list):
        out["form"] = "document_array_form"
        out["merged"] = [x for x in merged["document_findings"] if isinstance(x, dict)]
    else:
        out["form"] = "pre_adjudication"
        out["merged"] = iter_findings_list(merged)

    runtime = session_dir / "runtime"
    if runtime.is_dir():
        for seat_dir in sorted(runtime.iterdir()):
            if not seat_dir.is_dir():
                continue
            data = load_json(seat_dir / "findings.json")
            for f in iter_findings_list(data):
                row = dict(f)
                row.setdefault("reviewer", seat_dir.name)
                out["seat_emitted"].append(row)
    return out


def walk_runtime_seats(session_dir: Path, routed_seats: list[str] | None) -> list[dict[str, Any]]:
    """Authoritative runtime/<seat>/ when present; missing → not_run / unknown."""
    runtime_dir = session_dir / "runtime"
    seen: dict[str, dict[str, Any]] = {}
    if runtime_dir.is_dir():
        for seat_dir in sorted(runtime_dir.iterdir()):
            if not seat_dir.is_dir():
                continue
            result = load_json(seat_dir / "result.json") or {}
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            seat = str(result.get("seat") or seat_dir.name).lower()
            findings_data = load_json(seat_dir / "findings.json")
            raw_n = len(iter_findings_list(findings_data)) if findings_data is not None else None
            completed = result.get("completed")
            status = "completed" if completed is True else ("failed" if completed is False else "unknown")
            if not result and findings_data is None:
                status = "unknown"
            cost = usage.get("cost")
            seen[seat] = {
                "seat": seat,
                "status": status,
                "runtime": result.get("runtime"),
                "model": result.get("model_actual") or result.get("model_resolved"),
                "duration_ms": result.get("duration_ms"),
                "completed": completed,
                "schema_valid": result.get("schema_valid"),
                "attempts": result.get("attempts"),
                "failure_category": result.get("failure_category"),
                "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
                "tokens": usage.get("tokens"),
                "raw_findings": raw_n,
                "runtime_dir_present": True,
            }

    expected = list(routed_seats or []) or list(SEATS)
    out: list[dict[str, Any]] = []
    for seat in expected:
        seat = str(seat).lower()
        if seat in seen:
            out.append(seen.pop(seat))
        else:
            out.append(
                {
                    "seat": seat,
                    "status": "not_run",
                    "runtime": None,
                    "model": None,
                    "duration_ms": None,
                    "completed": None,
                    "schema_valid": None,
                    "attempts": None,
                    "failure_category": None,
                    "cost_usd": None,
                    "tokens": None,
                    "raw_findings": None,
                    "runtime_dir_present": False,
                }
            )
    # Extra seats present on disk but not in routing
    for seat, row in sorted(seen.items()):
        out.append(row)
    return out


def load_verifier_counts(session_dir: Path, metrics: dict[str, Any]) -> dict[str, int]:
    ver = (metrics.get("verification") or {}) if isinstance(metrics, dict) else {}
    confirmed = int(ver.get("confirmed") or 0)
    rejected = int(ver.get("rejected") or 0)
    inconclusive = int(ver.get("inconclusive") or 0)
    if confirmed + rejected + inconclusive == 0:
        for name in ("verification.json", "verifications.json"):
            data = load_json(session_dir / name)
            if data is None:
                continue
            items = (
                data
                if isinstance(data, list)
                else data.get("verifications")
                if isinstance(data, dict)
                else []
            )
            if not isinstance(items, list):
                items = [data] if isinstance(data, dict) and "verdict" in data else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                v = item.get("verdict")
                if v == "confirmed":
                    confirmed += 1
                elif v == "rejected":
                    rejected += 1
                elif v == "inconclusive":
                    inconclusive += 1
    return {
        "confirmed": confirmed,
        "rejected": rejected,
        "inconclusive": inconclusive,
    }


def cross_seat_duplicates(seat_emitted: list[dict[str, Any]]) -> dict[str, list[str]]:
    fp_seats: dict[str, set[str]] = defaultdict(set)
    for f in seat_emitted:
        seat = str(f.get("reviewer") or f.get("seat") or "unknown").lower()
        fp = finding_fingerprint(f)
        if fp:
            fp_seats[fp].add(seat)
    return {fp: sorted(seats) for fp, seats in fp_seats.items() if len(seats) >= 2}
