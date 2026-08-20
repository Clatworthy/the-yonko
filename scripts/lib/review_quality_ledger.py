"""Observational review-quality ledger for real Yonko runs.

Learning only - never feeds seating, routing, adjudication, or apply.
Human fields (reached_production, human_missed) stay null until annotated.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEDGER_SCHEMA = 1
LEDGER_NAME = "review-quality-ledger.jsonl"
ROLLUP_MD = "review-quality-rollup.md"
HUMAN_NULL = None

SEATS = ("shanks", "blackbeard", "buggy", "luffy")


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _norm_title(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t[:160]


def _finding_fingerprint(f: dict[str, Any]) -> str:
    title = _norm_title(str(f.get("title") or f.get("id") or ""))
    cat = str(f.get("category") or "").lower()
    locus = f.get("locus") if isinstance(f.get("locus"), dict) else {}
    path = str(locus.get("path") or "")
    return f"{cat}|{path}|{title}"


def _iter_findings_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("findings", "plan_findings", "document_findings", "accepted", "held"):
        arr = data.get(key)
        if isinstance(arr, list):
            return [x for x in arr if isinstance(x, dict)]
    return []


def _bucket_findings(session_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Return accepted/dropped/held/raw seat findings where present."""
    out: dict[str, list[dict[str, Any]]] = {
        "accepted": [],
        "dropped": [],
        "held": [],
        "seat_emitted": [],
        "merged": [],
    }
    merged = _load_json(session_dir / "findings.json")
    if merged is not None:
        if isinstance(merged, dict) and any(
            k in merged for k in ("accepted", "dropped", "held", "notes")
        ):
            for key in ("accepted", "dropped", "held"):
                arr = merged.get(key)
                if isinstance(arr, list):
                    out[key] = [x for x in arr if isinstance(x, dict)]
        else:
            out["merged"] = _iter_findings_list(merged)

    runtime = session_dir / "runtime"
    if runtime.is_dir():
        for seat_dir in sorted(runtime.iterdir()):
            if not seat_dir.is_dir():
                continue
            data = _load_json(seat_dir / "findings.json")
            for f in _iter_findings_list(data):
                row = dict(f)
                row.setdefault("reviewer", seat_dir.name)
                out["seat_emitted"].append(row)
    return out


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(100.0 * num / den, 1)


def build_row(session_dir: Path) -> dict[str, Any]:
    """Prefer projection from evaluation measurement when present.

    Ownership: capture writes measurement; this projects legacy ledger shape.
    Capture must not import this module.
    """
    session_dir = session_dir.resolve()
    measurement = _load_json(session_dir / "evaluation" / "review-measurement.json")
    if isinstance(measurement, dict) and measurement.get("session_id"):
        from lib.evaluation.ledger_projection import project_ledger_row  # noqa: E402

        return project_ledger_row(measurement, session_dir)
    return _build_row_legacy(session_dir)


def _build_row_legacy(session_dir: Path) -> dict[str, Any]:
    """Legacy ledger builder when evaluation capture did not run."""
    session_dir = session_dir.resolve()
    session = _load_json(session_dir / "session.json") or {}
    metrics = _load_json(session_dir / "metrics.json") or {}
    risk = _load_json(session_dir / "evidence" / "risk.json") or {}
    routing = _load_json(session_dir / "evidence" / "routing.json") or {}
    buckets = _bucket_findings(session_dir)

    seats_runtime: list[dict[str, Any]] = []
    cost_total = 0.0
    duration_by_seat: dict[str, int | None] = {}
    runtime_dir = session_dir / "runtime"
    if runtime_dir.is_dir():
        for seat_dir in sorted(runtime_dir.iterdir()):
            if not seat_dir.is_dir():
                continue
            result = _load_json(seat_dir / "result.json") or {}
            usage = result.get("usage") or {}
            cost = usage.get("cost")
            if isinstance(cost, (int, float)):
                cost_total += float(cost)
            seat = str(result.get("seat") or seat_dir.name)
            duration_by_seat[seat] = result.get("duration_ms")
            seats_runtime.append(
                {
                    "seat": seat,
                    "runtime": result.get("runtime"),
                    "model": result.get("model_actual") or result.get("model_resolved"),
                    "duration_ms": result.get("duration_ms"),
                    "completed": result.get("completed"),
                    "schema_valid": result.get("schema_valid"),
                    "attempts": result.get("attempts"),
                    "failure_category": result.get("failure_category"),
                    "cost_usd": cost,
                    "tokens": (usage.get("tokens") or None),
                }
            )

    # Unique accepted per seat: prefer adjudication accepted; else unique seat titles
    accepted = buckets["accepted"] or buckets["merged"]
    unique_accepted_by_seat: dict[str, int] = {s: 0 for s in SEATS}
    titles_accepted: dict[str, set[str]] = defaultdict(set)
    for f in accepted:
        seat = str(f.get("reviewer") or f.get("seat") or "unknown").lower()
        fp = _finding_fingerprint(f)
        if fp and fp not in titles_accepted[seat]:
            titles_accepted[seat].add(fp)
    for seat, titles in titles_accepted.items():
        unique_accepted_by_seat[seat] = len(titles)

    # If no merged/accepted yet, count unique emitted titles per seat (pre-adjudication)
    pre_adjudication = not buckets["accepted"] and not buckets["merged"]
    unique_emitted_by_seat: dict[str, int] = {s: 0 for s in SEATS}
    emitted_fps: dict[str, set[str]] = defaultdict(set)
    for f in buckets["seat_emitted"]:
        seat = str(f.get("reviewer") or f.get("seat") or "unknown").lower()
        fp = _finding_fingerprint(f)
        if fp:
            emitted_fps[seat].add(fp)
    for seat, fps in emitted_fps.items():
        unique_emitted_by_seat[seat] = len(fps)

    # Cross-seat duplicates: same fingerprint from 2+ seats
    fp_seats: dict[str, set[str]] = defaultdict(set)
    for f in buckets["seat_emitted"]:
        seat = str(f.get("reviewer") or f.get("seat") or "unknown").lower()
        fp = _finding_fingerprint(f)
        if fp:
            fp_seats[fp].add(seat)
    duplicate_fps = {fp: sorted(seats) for fp, seats in fp_seats.items() if len(seats) >= 2}
    duplicate_finding_count = len(duplicate_fps)

    # Chair rejection: dropped / (accepted+dropped+held) when bucket form present
    chair_accepted = len(buckets["accepted"])
    chair_dropped = len(buckets["dropped"])
    chair_held = len(buckets["held"])
    chair_den = chair_accepted + chair_dropped + chair_held
    chair_reject_rate = _rate(chair_dropped, chair_den)

    # Verifier from metrics or verification.json
    ver = (metrics.get("verification") or {}) if isinstance(metrics, dict) else {}
    ver_confirmed = int(ver.get("confirmed") or 0)
    ver_rejected = int(ver.get("rejected") or 0)
    ver_inconclusive = int(ver.get("inconclusive") or 0)
    if ver_confirmed + ver_rejected + ver_inconclusive == 0:
        for name in ("verification.json", "verifications.json"):
            data = _load_json(session_dir / name)
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
                    ver_confirmed += 1
                elif v == "rejected":
                    ver_rejected += 1
                elif v == "inconclusive":
                    ver_inconclusive += 1
    ver_total = ver_confirmed + ver_rejected + ver_inconclusive
    verifier_reject_rate = _rate(ver_rejected, ver_total)

    # Severity changes: finding has original_severity / severity_before != severity
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
                    "reviewer": f.get("reviewer") or f.get("seat"),
                }
            )

    # Human outcome overlay (session-local file, optional)
    human = _load_json(session_dir / "review-quality-human.json") or {}

    wall_ms = None
    durs = [d for d in duration_by_seat.values() if isinstance(d, (int, float))]
    if durs:
        wall_ms = int(max(durs))  # parallel lower bound; prefer metrics duration if present
    if isinstance(metrics.get("duration_seconds"), (int, float)):
        wall_from_metrics = int(metrics["duration_seconds"] * 1000)
    else:
        wall_from_metrics = None

    row = {
        "schema_version": LEDGER_SCHEMA,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": session.get("session_id") or session_dir.name,
        "session_path": str(session_dir),
        "review_type": session.get("review_type") or metrics.get("review_type") or "implementation",
        "risk_band": risk.get("risk") or metrics.get("risk"),
        "verdict": metrics.get("verdict"),
        "pre_adjudication": pre_adjudication,
        "runtime": {
            "wall_duration_ms_estimate": wall_from_metrics or wall_ms,
            "max_seat_duration_ms": int(max(durs)) if durs else None,
            "sum_seat_duration_ms": int(sum(durs)) if durs else None,
            "duration_seconds_metrics": metrics.get("duration_seconds"),
            "seats": seats_runtime,
        },
        "cost": {
            "total_opencode_usd": round(cost_total, 6) if cost_total else 0.0,
            "per_seat_usd": {
                s["seat"]: s.get("cost_usd") for s in seats_runtime if s.get("cost_usd") is not None
            },
        },
        "findings": {
            "unique_accepted_by_seat": unique_accepted_by_seat,
            "unique_emitted_by_seat": unique_emitted_by_seat,
            "accepted_count": chair_accepted if buckets["accepted"] else len(buckets["merged"]),
            "dropped_count": chair_dropped,
            "held_count": chair_held,
            "emitted_count": len(buckets["seat_emitted"]),
            "duplicate_cross_seat_count": duplicate_finding_count,
            "duplicate_fingerprints": [
                {"fingerprint": fp, "seats": seats} for fp, seats in sorted(duplicate_fps.items())
            ],
        },
        "verifier": {
            "confirmed": ver_confirmed,
            "rejected": ver_rejected,
            "inconclusive": ver_inconclusive,
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
        "human": {
            "reached_production": human.get("reached_production", HUMAN_NULL),
            "reviewer_found_human_missed": human.get(
                "reviewer_found_human_missed", HUMAN_NULL
            ),
            "notes": human.get("notes"),
            "annotated_at": human.get("annotated_at"),
            "finding_annotations": human.get("finding_annotations") or {},
        },
        "routing_seats": routing.get("seats"),
        "packet_bytes": metrics.get("packet_bytes"),
        "packet_hash": metrics.get("packet_hash") or session.get("packet_hash"),
        "gaps": [],
        "policy": "learning_only_never_auto_tune",
    }

    gaps = []
    if pre_adjudication:
        gaps.append("no_session_findings_adjudication_yet")
    if chair_reject_rate is None:
        gaps.append("chair_reject_rate_unavailable")
    if verifier_reject_rate is None:
        gaps.append("verifier_data_unavailable")
    if not severity_changes and (accepted or buckets["merged"]):
        gaps.append("no_severity_change_fields_recorded")
    if row["human"]["reached_production"] is None:
        gaps.append("human_reached_production_unset")
    if row["human"]["reviewer_found_human_missed"] is None:
        gaps.append("human_missed_unset")
    row["gaps"] = gaps
    return row


def ledger_path(sessions_root: Path) -> Path:
    return sessions_root / "_rollup" / LEDGER_NAME


def load_ledger(sessions_root: Path) -> list[dict[str, Any]]:
    path = ledger_path(sessions_root)
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


def upsert_row(sessions_root: Path, row: dict[str, Any]) -> Path:
    path = ledger_path(sessions_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_ledger(sessions_root)
    sid = row.get("session_id")
    kept = [r for r in existing if r.get("session_id") != sid]
    kept.append(row)
    # Stable-ish: newest last
    text = "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in kept)
    path.write_text(text, encoding="utf-8")
    # Also write per-session snapshot
    session_path = Path(row["session_path"])
    if session_path.is_dir():
        (session_path / "review-quality.json").write_text(
            json.dumps(row, indent=2) + "\n", encoding="utf-8"
        )
    return path


def annotate_human(
    session_dir: Path,
    *,
    reached_production: str | None = None,
    reviewer_found_human_missed: str | None = None,
    notes: str | None = None,
    finding_id: str | None = None,
    finding_reached_production: str | None = None,
    finding_human_missed: str | None = None,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    path = session_dir / "review-quality-human.json"
    data = _load_json(path) or {}
    allowed = {"yes", "no", "unknown"}

    def _norm(v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in allowed:
            raise ValueError(f"value must be yes|no|unknown, got {v!r}")
        return v

    if reached_production is not None:
        data["reached_production"] = _norm(reached_production)
    if reviewer_found_human_missed is not None:
        data["reviewer_found_human_missed"] = _norm(reviewer_found_human_missed)
    if notes is not None:
        data["notes"] = notes
    anns = data.setdefault("finding_annotations", {})
    if finding_id:
        entry = anns.setdefault(finding_id, {})
        if finding_reached_production is not None:
            entry["reached_production"] = _norm(finding_reached_production)
        if finding_human_missed is not None:
            entry["reviewer_found_human_missed"] = _norm(finding_human_missed)
    data["annotated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def write_rollup(sessions_root: Path) -> dict[str, Any]:
    rows = load_ledger(sessions_root)
    n = len(rows)
    unique_sum: Counter[str] = Counter()
    emitted_sum: Counter[str] = Counter()
    dup_total = 0
    ver_r = ver_t = 0
    chair_d = chair_t = 0
    sev_changes = 0
    costs = []
    walls = []
    reached = Counter()
    missed = Counter()
    gaps = Counter()

    for r in rows:
        for seat, c in (r.get("findings") or {}).get("unique_accepted_by_seat", {}).items():
            unique_sum[seat] += int(c or 0)
        for seat, c in (r.get("findings") or {}).get("unique_emitted_by_seat", {}).items():
            emitted_sum[seat] += int(c or 0)
        dup_total += int((r.get("findings") or {}).get("duplicate_cross_seat_count") or 0)
        v = r.get("verifier") or {}
        vt = int(v.get("confirmed") or 0) + int(v.get("rejected") or 0) + int(v.get("inconclusive") or 0)
        ver_t += vt
        ver_r += int(v.get("rejected") or 0)
        c = r.get("chair") or {}
        ct = int(c.get("accepted") or 0) + int(c.get("dropped") or 0) + int(c.get("held") or 0)
        chair_t += ct
        chair_d += int(c.get("dropped") or 0)
        sev_changes += int(r.get("severity_change_count") or 0)
        cost = (r.get("cost") or {}).get("total_opencode_usd")
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
        wall = (r.get("runtime") or {}).get("wall_duration_ms_estimate")
        if isinstance(wall, (int, float)):
            walls.append(int(wall))
        h = r.get("human") or {}
        reached[str(h.get("reached_production"))] += 1
        missed[str(h.get("reviewer_found_human_missed"))] += 1
        for g in r.get("gaps") or []:
            gaps[g] += 1

    rollup = {
        "schema_version": LEDGER_SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions": n,
        "unique_accepted_by_seat_sum": dict(unique_sum),
        "unique_emitted_by_seat_sum": dict(emitted_sum),
        "duplicate_cross_seat_total": dup_total,
        "verifier_reject_rate_percent": _rate(ver_r, ver_t),
        "chair_reject_rate_percent": _rate(chair_d, chair_t),
        "severity_change_total": sev_changes,
        "average_cost_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "average_wall_ms": int(sum(walls) / len(walls)) if walls else None,
        "human_reached_production": dict(reached),
        "human_missed": dict(missed),
        "gap_counts": dict(gaps),
        "policy": "learning_only_never_auto_tune",
        "target_sample_size": "30-50 real reviews",
    }

    out_dir = sessions_root / "_rollup"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review-quality-rollup.json").write_text(
        json.dumps(rollup, indent=2) + "\n", encoding="utf-8"
    )

    def fmt_ms(ms: int | None) -> str:
        if ms is None:
            return "n/a"
        sec = ms // 1000
        m, s = divmod(sec, 60)
        return f"{m}m {s}s"

    md = f"""# Yonko review-quality rollup

Generated: `{rollup['generated_at']}`
Sessions in ledger: **{n}** (target 30-50)
Policy: learning only - never auto-tune seating/routing/apply

## Quality

- Unique accepted by seat (sum): `{dict(unique_sum)}`
- Unique emitted by seat (sum): `{dict(emitted_sum)}`
- Cross-seat duplicate clusters: **{dup_total}**
- Verifier reject rate: `{rollup['verifier_reject_rate_percent']}%`
- Chair reject rate: `{rollup['chair_reject_rate_percent']}%`
- Severity changes recorded: **{sev_changes}**

## Cost / runtime

- Average OpenCode cost USD: `{rollup['average_cost_usd']}`
- Average wall duration: `{fmt_ms(rollup['average_wall_ms'])}`

## Human outcomes (annotate after the fact)

- reached_production: `{dict(reached)}`
- reviewer_found_human_missed: `{dict(missed)}`

## Gaps (count of sessions missing a field)

{chr(10).join(f'- {k}: {v}' for k, v in sorted(gaps.items())) or '- none'}

## How to annotate

```bash
scripts/review-quality-ledger.sh --session DIR --annotate \\
  --reached-prod yes|no|unknown \\
  --human-missed yes|no|unknown \\
  [--notes TEXT]
```
"""
    (out_dir / ROLLUP_MD).write_text(md, encoding="utf-8")
    return rollup
