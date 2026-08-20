"""Council effectiveness report from measurement (observational)."""

from __future__ import annotations

from typing import Any

from .config import load_evaluation_yaml


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(100.0 * num / den, 1)


def build_council_effectiveness(measurement: dict[str, Any]) -> dict[str, Any]:
    cfg = load_evaluation_yaml()
    min_n = int(cfg.get("min_sample_n") or 10)

    seats_in = measurement.get("seats") if isinstance(measurement.get("seats"), list) else []
    findings = measurement.get("findings") if isinstance(measurement.get("findings"), dict) else {}
    dispositions = (
        measurement.get("dispositions") if isinstance(measurement.get("dispositions"), dict) else {}
    )

    seat_rows = []
    for s in seats_in:
        if not isinstance(s, dict):
            continue
        seat = s.get("seat")
        accepted_map = findings.get("unique_accepted_by_seat") or {}
        emitted_map = findings.get("unique_emitted_by_seat") or {}
        seat_rows.append(
            {
                "schema_version": 1,
                "seat": seat,
                "status": s.get("status") or "unknown",
                "raw_findings": s.get("raw_findings"),
                "accepted_unique": accepted_map.get(seat) if isinstance(accepted_map, dict) else None,
                "emitted_unique": emitted_map.get(seat) if isinstance(emitted_map, dict) else None,
                "duplicates": None,
                "duration_ms": s.get("duration_ms"),
                "cost_usd": s.get("cost_usd"),
                "runtime": s.get("runtime"),
                "model": s.get("model"),
            }
        )

    totals = {
        "accepted_count": findings.get("accepted_count") or 0,
        "dropped_count": findings.get("dropped_count") or 0,
        "held_count": findings.get("held_count") or 0,
        "emitted_count": findings.get("emitted_count") or 0,
        "duplicate_cross_seat_count": findings.get("duplicate_cross_seat_count") or 0,
        "disposition_counts": dispositions.get("counts") or {},
        "path_quality_status": (measurement.get("path_quality") or {}).get("status"),
        "adjudication_state": measurement.get("adjudication_state"),
        "cost_usd": (measurement.get("cost") or {}).get("total_opencode_usd"),
    }

    # Per-session sample is always 1; insufficient_sample for strong claims needs aggregate N.
    sample_warning = None
    session_n = 1
    insufficient_sample = session_n < min_n

    return {
        "schema_version": 1,
        "session_id": measurement.get("session_id"),
        "review_type": measurement.get("review_type"),
        "packet_hash": measurement.get("packet_hash"),
        "totals": totals,
        "seats": seat_rows,
        "flags": list(measurement.get("flags") or []),
        "min_sample_n": min_n,
        "sample_size": session_n,
        "insufficient_sample": insufficient_sample,
        "sample_warning": (
            f"insufficient_sample: session-level report; aggregate n < {min_n} required for strong claims"
            if insufficient_sample
            else sample_warning
        ),
        "policy": "observational_suggest_only_never_auto_tune",
        "chair_reject_rate_percent": _rate(
            int(totals["dropped_count"]),
            int(totals["accepted_count"])
            + int(totals["dropped_count"])
            + int(totals["held_count"]),
        ),
    }


def effectiveness_markdown(report: dict[str, Any]) -> str:
    seats = report.get("seats") or []
    seat_lines = []
    for s in seats:
        seat_lines.append(
            f"- `{s.get('seat')}`: status={s.get('status')} raw={s.get('raw_findings')} "
            f"accepted_unique={s.get('accepted_unique')} duration_ms={s.get('duration_ms')} "
            f"cost_usd={s.get('cost_usd')}"
        )
    totals = report.get("totals") or {}
    flags = report.get("flags") or []
    return f"""# Council effectiveness

Session: `{report.get('session_id')}`
Review type: `{report.get('review_type')}`
Packet: `{(report.get('packet_hash') or '')[:16]}`
Policy: observational / suggest-only - never auto-tune

## Totals

- Accepted: **{totals.get('accepted_count')}**
- Dropped: **{totals.get('dropped_count')}**
- Held: **{totals.get('held_count')}**
- Emitted (runtime): **{totals.get('emitted_count')}**
- Cross-seat duplicates: **{totals.get('duplicate_cross_seat_count')}**
- Path quality: `{totals.get('path_quality_status')}`
- Adjudication state: `{totals.get('adjudication_state')}`
- Chair reject rate %: `{report.get('chair_reject_rate_percent')}`
- Cost USD: `{totals.get('cost_usd')}`

## Sample

- sample_size (this report): **{report.get('sample_size')}**
- min_sample_n: **{report.get('min_sample_n')}**
- insufficient_sample: **{report.get('insufficient_sample')}**
- warning: {report.get('sample_warning') or 'none'}

## Flags

{chr(10).join(f'- {f}' for f in flags) or '- none'}

## Seats

{chr(10).join(seat_lines) or '- none'}
"""
