"""Project legacy review-quality ledger row from review-measurement.

Capture never imports the ledger. Ledger may import this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .facts import load_json

LEDGER_SCHEMA = 1
HUMAN_NULL = None


def project_ledger_row(measurement: dict[str, Any], session_dir: Path) -> dict[str, Any]:
    """Build backward-compatible ledger shape from canonical measurement."""
    session_dir = session_dir.resolve()
    human = load_json(session_dir / "review-quality-human.json") or {}
    seats = measurement.get("seats") if isinstance(measurement.get("seats"), list) else []
    findings = measurement.get("findings") if isinstance(measurement.get("findings"), dict) else {}
    verifier = measurement.get("verifier") if isinstance(measurement.get("verifier"), dict) else {}
    chair = measurement.get("chair") if isinstance(measurement.get("chair"), dict) else {}
    cost = measurement.get("cost") if isinstance(measurement.get("cost"), dict) else {}
    runtime = measurement.get("runtime") if isinstance(measurement.get("runtime"), dict) else {}

    seats_runtime = []
    for s in seats:
        if not isinstance(s, dict):
            continue
        seats_runtime.append(
            {
                "seat": s.get("seat"),
                "runtime": s.get("runtime"),
                "model": s.get("model"),
                "duration_ms": s.get("duration_ms"),
                "completed": s.get("completed"),
                "schema_valid": s.get("schema_valid"),
                "attempts": s.get("attempts"),
                "failure_category": s.get("failure_category"),
                "cost_usd": s.get("cost_usd"),
                "tokens": s.get("tokens"),
            }
        )

    pre = measurement.get("adjudication_state") in (
        "pre_adjudication",
        "plan_array_form",
        "document_array_form",
    )

    row = {
        "schema_version": LEDGER_SCHEMA,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": measurement.get("session_id") or session_dir.name,
        "session_path": str(session_dir),
        "review_type": measurement.get("review_type") or "implementation",
        "risk_band": measurement.get("risk_band"),
        "verdict": measurement.get("verdict"),
        "pre_adjudication": pre,
        "runtime": {
            "wall_duration_ms_estimate": runtime.get("wall_duration_ms_estimate"),
            "max_seat_duration_ms": runtime.get("max_seat_duration_ms"),
            "sum_seat_duration_ms": runtime.get("sum_seat_duration_ms"),
            "duration_seconds_metrics": runtime.get("duration_seconds_metrics"),
            "seats": seats_runtime,
        },
        "cost": {
            "total_opencode_usd": cost.get("total_opencode_usd") or 0.0,
            "per_seat_usd": cost.get("per_seat_usd") or {},
        },
        "findings": {
            "unique_accepted_by_seat": findings.get("unique_accepted_by_seat") or {},
            "unique_emitted_by_seat": findings.get("unique_emitted_by_seat") or {},
            "accepted_count": findings.get("accepted_count") or 0,
            "dropped_count": findings.get("dropped_count") or 0,
            "held_count": findings.get("held_count") or 0,
            "emitted_count": findings.get("emitted_count") or 0,
            "duplicate_cross_seat_count": findings.get("duplicate_cross_seat_count") or 0,
            "duplicate_fingerprints": findings.get("duplicate_fingerprints") or [],
        },
        "verifier": {
            "confirmed": verifier.get("confirmed") or 0,
            "rejected": verifier.get("rejected") or 0,
            "inconclusive": verifier.get("inconclusive") or 0,
            "reject_rate_percent": verifier.get("reject_rate_percent"),
        },
        "chair": {
            "accepted": chair.get("accepted") or 0,
            "dropped": chair.get("dropped") or 0,
            "held": chair.get("held") or 0,
            "reject_rate_percent": chair.get("reject_rate_percent"),
        },
        "severity_changes": measurement.get("severity_changes") or [],
        "severity_change_count": measurement.get("severity_change_count") or 0,
        "human": {
            "reached_production": human.get("reached_production", HUMAN_NULL),
            "reviewer_found_human_missed": human.get(
                "reviewer_found_human_missed", HUMAN_NULL
            ),
            "notes": human.get("notes"),
            "annotated_at": human.get("annotated_at"),
            "finding_annotations": human.get("finding_annotations") or {},
        },
        "routing_seats": measurement.get("routing_seats"),
        "packet_bytes": measurement.get("packet_bytes"),
        "packet_hash": measurement.get("packet_hash"),
        "gaps": list(measurement.get("ledger_gaps") or []),
        "policy": "learning_only_never_auto_tune",
        "measurement_schema_version": measurement.get("schema_version"),
        "adjudication_state": measurement.get("adjudication_state"),
        "evaluation_projection": True,
    }

    # Preserve legacy gap semantics for human fields
    gaps = list(row["gaps"])
    if row["human"]["reached_production"] is None and "human_reached_production_unset" not in gaps:
        gaps.append("human_reached_production_unset")
    if row["human"]["reviewer_found_human_missed"] is None and "human_missed_unset" not in gaps:
        gaps.append("human_missed_unset")
    row["gaps"] = gaps
    return row
