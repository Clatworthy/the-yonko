"""Canonical session observability capture (evaluation SoT).

Ownership (no circular capture↔ledger):
  capture_session_observability()
    → review-measurement.json
    → council-effectiveness
    → ledger projection (returned; finalize upserts)

Never imports review_quality_ledger.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_observability_evaluation, measurement_index_path, sessions_root
from .effectiveness import build_council_effectiveness, effectiveness_markdown
from .facts import (
    SEATS,
    bucket_findings,
    cross_seat_duplicates,
    finding_fingerprint,
    load_json,
    load_verifier_counts,
    walk_runtime_seats,
)
from .index import upsert_index_entry
from .io import atomic_multi_write, write_text
from .ledger_projection import project_ledger_row
from .path_quality import assess_path_quality
from .taxonomy import (
    map_accepted_disposition,
    map_dropped_disposition,
    map_held_disposition,
    seat_from_finding,
)


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(100.0 * num / den, 1)


def _adjudication_state(buckets: dict[str, Any]) -> str:
    form = buckets.get("form")
    accepted = buckets.get("accepted") or []
    dropped = buckets.get("dropped") or []
    held = buckets.get("held") or []
    merged = buckets.get("merged") or []
    emitted = buckets.get("seat_emitted") or []

    if form == "plan_array_form":
        return "plan_array_form"
    if form == "document_array_form":
        return "document_array_form"
    if form == "adjudication_buckets":
        if not accepted and not dropped and not held:
            return "empty_findings"
        if accepted or dropped or held:
            # partial if some seats emitted but chair buckets empty of accepted while dropped only?
            return "complete"
        return "empty_findings"
    if form == "pre_adjudication":
        if not merged and not emitted:
            return "empty_findings"
        return "pre_adjudication"
    if form == "missing":
        if not emitted:
            return "empty_findings"
        return "pre_adjudication"
    return "pre_adjudication"


def _build_dispositions(buckets: dict[str, Any], state: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    if state in ("pre_adjudication", "plan_array_form", "document_array_form"):
        for f in (buckets.get("merged") or []) + (buckets.get("seat_emitted") or []):
            if not isinstance(f, dict):
                continue
            disp = "unknown_not_adjudicated"
            counts[disp] += 1
            items.append(
                {
                    "schema_version": 1,
                    "finding_id": f.get("id"),
                    "disposition": disp,
                    "seat": seat_from_finding(f),
                    "provenance": {"source": state},
                    "title": f.get("title"),
                }
            )
        return {"items": items, "counts": dict(counts)}

    if state == "empty_findings":
        return {"items": [], "counts": {}}

    # Dedup accepted by fingerprint for duplicate disposition
    seen_accepted_fp: dict[str, str] = {}
    for f in buckets.get("accepted") or []:
        if not isinstance(f, dict):
            continue
        fp = finding_fingerprint(f)
        duplicate = bool(fp and fp in seen_accepted_fp)
        if fp and not duplicate:
            seen_accepted_fp[fp] = str(f.get("id") or fp)
        disp = map_accepted_disposition(f, duplicate=duplicate)
        counts[disp] += 1
        items.append(
            {
                "schema_version": 1,
                "finding_id": f.get("id"),
                "disposition": disp,
                "seat": seat_from_finding(f),
                "provenance": {"source": "accepted"},
                "title": f.get("title"),
            }
        )

    for f in buckets.get("dropped") or []:
        if not isinstance(f, dict):
            continue
        disp = map_dropped_disposition(f)
        counts[disp] += 1
        items.append(
            {
                "schema_version": 1,
                "finding_id": f.get("id"),
                "disposition": disp,
                "seat": seat_from_finding(f),
                "provenance": {
                    "source": "dropped",
                    "action": f.get("action"),
                    "reason_present": bool(
                        str(f.get("reason") or f.get("drop_reason") or "").strip()
                    ),
                },
                "title": f.get("title"),
            }
        )

    for f in buckets.get("held") or []:
        if not isinstance(f, dict):
            continue
        disp = map_held_disposition(f)
        counts[disp] += 1
        items.append(
            {
                "schema_version": 1,
                "finding_id": f.get("id"),
                "disposition": disp,
                "seat": seat_from_finding(f),
                "provenance": {"source": "held"},
                "title": f.get("title"),
            }
        )

    return {"items": items, "counts": dict(counts)}


def build_measurement(session_dir: Path) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    session = load_json(session_dir / "session.json") or {}
    metrics = load_json(session_dir / "metrics.json") or {}
    outcome = load_json(session_dir / "outcome.json")
    risk = load_json(session_dir / "evidence" / "risk.json") or {}
    routing = load_json(session_dir / "evidence" / "routing.json") or {}
    packet_meta = load_json(session_dir / "packet.meta.json") or {}

    buckets = bucket_findings(session_dir)
    state = _adjudication_state(buckets)
    routed = routing.get("seats") if isinstance(routing.get("seats"), list) else None
    seats = walk_runtime_seats(session_dir, routed)

    accepted = buckets["accepted"] or buckets["merged"]
    unique_accepted_by_seat: dict[str, int] = {s: 0 for s in SEATS}
    titles_accepted: dict[str, set[str]] = defaultdict(set)
    for f in accepted:
        seat = str(seat_from_finding(f) or "unknown").lower()
        fp = finding_fingerprint(f)
        if fp and fp not in titles_accepted[seat]:
            titles_accepted[seat].add(fp)
    for seat, titles in titles_accepted.items():
        unique_accepted_by_seat[seat] = len(titles)

    unique_emitted_by_seat: dict[str, int] = {s: 0 for s in SEATS}
    emitted_fps: dict[str, set[str]] = defaultdict(set)
    for f in buckets["seat_emitted"]:
        seat = str(seat_from_finding(f) or "unknown").lower()
        fp = finding_fingerprint(f)
        if fp:
            emitted_fps[seat].add(fp)
    for seat, fps in emitted_fps.items():
        unique_emitted_by_seat[seat] = len(fps)

    duplicate_fps = cross_seat_duplicates(buckets["seat_emitted"])

    chair_accepted = len(buckets["accepted"])
    chair_dropped = len(buckets["dropped"])
    chair_held = len(buckets["held"])
    chair_den = chair_accepted + chair_dropped + chair_held
    chair_reject_rate = _rate(chair_dropped, chair_den)

    ver = load_verifier_counts(session_dir, metrics if isinstance(metrics, dict) else {})
    ver_total = ver["confirmed"] + ver["rejected"] + ver["inconclusive"]
    verifier_reject_rate = _rate(ver["rejected"], ver_total)

    severity_changes: list[dict[str, Any]] = []
    for f in accepted + buckets["dropped"] + buckets["merged"]:
        before = f.get("original_severity") or f.get("severity_before") or f.get("seat_severity")
        after = f.get("severity")
        if before and after and str(before).lower() != str(after).lower():
            severity_changes.append(
                {
                    "id": f.get("id"),
                    "from": before,
                    "to": after,
                    "reviewer": seat_from_finding(f),
                }
            )

    cost_total = 0.0
    per_seat_usd: dict[str, float] = {}
    durs: list[int] = []
    for s in seats:
        c = s.get("cost_usd")
        if isinstance(c, (int, float)):
            cost_total += float(c)
            per_seat_usd[str(s.get("seat"))] = float(c)
        d = s.get("duration_ms")
        if isinstance(d, (int, float)):
            durs.append(int(d))

    wall_ms = int(max(durs)) if durs else None
    if isinstance(metrics.get("duration_seconds"), (int, float)):
        wall_from_metrics = int(metrics["duration_seconds"] * 1000)
    else:
        wall_from_metrics = None

    review_type = (
        session.get("review_type") or metrics.get("review_type") or "implementation"
    )
    seats_completed = any(s.get("status") == "completed" for s in seats)

    # Path quality over accepted / merged findings used for review
    pq_findings = list(buckets["accepted"] or buckets["merged"] or [])
    path_quality = assess_path_quality(
        review_type=str(review_type),
        findings=pq_findings,
        seats_completed=seats_completed,
    )

    flags: list[str] = []
    if state == "empty_findings":
        flags.append("empty_findings")
    if not (session_dir / "runtime").is_dir():
        flags.append("runtime_missing")
    if any(s.get("status") == "not_run" for s in seats):
        flags.append("runtime_seat_not_run")
    if path_quality.get("status") == "not_applicable":
        flags.append("path_quality_not_applicable")
    if path_quality.get("status") == "fail":
        flags.append("path_quality_fail")

    dispositions = _build_dispositions(buckets, state)

    packet_hash = (
        packet_meta.get("packet_hash")
        or metrics.get("packet_hash")
        or session.get("packet_hash")
    )

    ledger_gaps: list[str] = []
    if state in ("pre_adjudication", "plan_array_form", "document_array_form"):
        ledger_gaps.append("no_session_findings_adjudication_yet")
    if chair_reject_rate is None:
        ledger_gaps.append("chair_reject_rate_unavailable")
    if verifier_reject_rate is None:
        ledger_gaps.append("verifier_data_unavailable")
    if not severity_changes and (accepted or buckets["merged"]):
        ledger_gaps.append("no_severity_change_fields_recorded")

    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": session.get("session_id") or session_dir.name,
        "session_path": str(session_dir),
        "review_type": review_type,
        "risk_band": risk.get("risk") or metrics.get("risk"),
        "verdict": metrics.get("verdict"),
        "packet_hash": packet_hash,
        "packet_bytes": metrics.get("packet_bytes") or packet_meta.get("packet_bytes"),
        "outcome_axes": outcome if isinstance(outcome, dict) else None,
        "adjudication_state": state,
        "findings_form": buckets.get("form"),
        "notes_count": buckets.get("notes_count") or 0,
        "findings": {
            "unique_accepted_by_seat": unique_accepted_by_seat,
            "unique_emitted_by_seat": unique_emitted_by_seat,
            "accepted_count": chair_accepted if buckets["accepted"] else len(buckets["merged"]),
            "dropped_count": chair_dropped,
            "held_count": chair_held,
            "emitted_count": len(buckets["seat_emitted"]),
            "duplicate_cross_seat_count": len(duplicate_fps),
            "duplicate_fingerprints": [
                {"fingerprint": fp, "seats": seat_list}
                for fp, seat_list in sorted(duplicate_fps.items())
            ],
        },
        "dispositions": dispositions,
        "seats": seats,
        "runtime": {
            "wall_duration_ms_estimate": wall_from_metrics or wall_ms,
            "max_seat_duration_ms": int(max(durs)) if durs else None,
            "sum_seat_duration_ms": int(sum(durs)) if durs else None,
            "duration_seconds_metrics": metrics.get("duration_seconds"),
            "runtime_dir_present": (session_dir / "runtime").is_dir(),
        },
        "cost": {
            "total_opencode_usd": round(cost_total, 6) if cost_total else 0.0,
            "per_seat_usd": per_seat_usd,
        },
        "verifier": {
            "confirmed": ver["confirmed"],
            "rejected": ver["rejected"],
            "inconclusive": ver["inconclusive"],
            "reject_rate_percent": verifier_reject_rate,
        },
        "chair": {
            "accepted": chair_accepted,
            "dropped": chair_dropped,
            "held": chair_held,
            "reject_rate_percent": chair_reject_rate,
        },
        "severity_changes": severity_changes,
        "severity_change_count": len(severity_changes),
        "routing_seats": routed,
        "path_quality": path_quality,
        "flags": flags,
        "ledger_gaps": ledger_gaps,
        "policy": "observational_never_auto_tune",
    }


def build_eval_candidate(measurement: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    flags = set(measurement.get("flags") or [])
    state = measurement.get("adjudication_state")
    findings = measurement.get("findings") or {}
    accepted = int(findings.get("accepted_count") or 0)
    dropped = int(findings.get("dropped_count") or 0)
    emitted = int(findings.get("emitted_count") or 0)

    if "empty_findings" in flags or state == "empty_findings":
        reasons.append("weak_or_empty")
    elif accepted + dropped == 0 and emitted == 0:
        reasons.append("weak_or_empty")
    else:
        if state == "complete":
            reasons.append("adjudication_complete")
        if accepted > 0:
            reasons.append("has_accepted_findings")
        if dropped > 0:
            reasons.append("has_dropped_findings")
        pq = (measurement.get("path_quality") or {}).get("status")
        if pq == "pass":
            reasons.append("path_quality_pass")
        if measurement.get("packet_hash"):
            reasons.append("packet_hash_present")

    # Never strong-mark empty findings
    strong = [
        r
        for r in reasons
        if r not in ("weak_or_empty",)
    ]
    if "weak_or_empty" in reasons:
        strong = []

    return {
        "schema_version": 1,
        "session_id": measurement.get("session_id"),
        "packet_hash": measurement.get("packet_hash"),
        "reasons": reasons,
        "strong_reasons": strong,
        "promoted": False,
        "flags": list(flags),
        "note": "candidate only - promote via promote-case.sh with human approval",
    }


def capture_session_observability(
    session_dir: Path,
    *,
    write: bool = True,
    upsert_index: bool = True,
    sessions_root_override: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally persist evaluation artefacts + ledger projection."""
    session_dir = session_dir.resolve()
    measurement = build_measurement(session_dir)
    effectiveness = build_council_effectiveness(measurement)
    candidate = build_eval_candidate(measurement)
    ledger_row = project_ledger_row(measurement, session_dir)
    md = effectiveness_markdown(effectiveness)

    result = {
        "ok": True,
        "measurement": measurement,
        "effectiveness": effectiveness,
        "candidate": candidate,
        "ledger_row": ledger_row,
        "paths": {},
    }

    if write:
        eval_dir = session_dir / "evaluation"
        files = {
            "review-measurement.json": json.dumps(measurement, indent=2) + "\n",
            "council-effectiveness.json": json.dumps(effectiveness, indent=2) + "\n",
            "council-effectiveness.md": md,
            "eval-candidate.json": json.dumps(candidate, indent=2) + "\n",
        }
        atomic_multi_write(eval_dir, files)
        result["paths"] = {
            "measurement": str(eval_dir / "review-measurement.json"),
            "effectiveness": str(eval_dir / "council-effectiveness.json"),
            "candidate": str(eval_dir / "eval-candidate.json"),
        }
        err = session_dir / "evaluation" / "capture.error.txt"
        if err.is_file():
            err.unlink()

        if upsert_index:
            root = sessions_root_override or sessions_root()
            # Prefer writing index under the same sessions root as ledger when overridden
            idx_path = (
                root / "_rollup" / "measurement-index.jsonl"
                if sessions_root_override is not None
                else measurement_index_path()
            )
            entry = {
                "schema_version": 1,
                "session_id": measurement.get("session_id"),
                "packet_hash": measurement.get("packet_hash"),
                "completed_at": measurement.get("captured_at"),
                "review_type": measurement.get("review_type"),
                "adjudication_state": measurement.get("adjudication_state"),
                "eval_candidate": True,
                "flags": measurement.get("flags") or [],
                "session_path": str(session_dir),
            }
            upsert_index_entry(entry, path=idx_path)
            result["paths"]["measurement_index"] = str(idx_path)

    return result


def capture_or_fail_open(
    session_dir: Path,
    *,
    sessions_root_override: Path | None = None,
) -> dict[str, Any]:
    """Respect observability-policy evaluation.fail_open / capture_on_finalize."""
    import os

    policy = load_observability_evaluation()
    if not policy.get("capture_on_finalize", True):
        return {"ok": True, "skipped": True, "reason": "capture_on_finalize=false"}
    try:
        # Test/debug only: finalize subprocess cannot use unittest.mock.
        if os.environ.get("YONKO_EVAL_FORCE_CAPTURE_FAIL") == "1":
            raise RuntimeError("YONKO_EVAL_FORCE_CAPTURE_FAIL")
        return capture_session_observability(
            session_dir,
            write=True,
            upsert_index=True,
            sessions_root_override=sessions_root_override,
        )
    except Exception as exc:
        eval_dir = session_dir / "evaluation"
        eval_dir.mkdir(parents=True, exist_ok=True)
        write_text(eval_dir / "capture.error.txt", f"capture_session_observability: {exc}\n")
        if policy.get("fail_open", True):
            return {"ok": False, "fail_open": True, "error": str(exc)}
        raise
