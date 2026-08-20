#!/usr/bin/env bash
# aggregate-metrics.sh - read-only rollup across yonko-sessions (V2.1 learning only).
# NEVER feeds routing, adjudication, or apply decisions.
#
# Usage: aggregate-metrics.sh [--limit N] [--type implementation|plan|document]
# Writes: ~/.cursor/yonko-sessions/_rollup/metrics-rollup.json (+ .md)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

LIMIT=0
TYPE_FILTER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="${2:-0}"; shift 2 ;;
    --type) TYPE_FILTER="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: aggregate-metrics.sh [--limit N] [--type implementation|plan|document]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

if [[ -n "$TYPE_FILTER" ]]; then
  case "$TYPE_FILTER" in
    implementation|plan|document) ;;
    *) yonko_die "--type must be implementation|plan|document" ;;
  esac
fi

python3 - "$YONKO_SESSIONS_ROOT" "$LIMIT" "$TYPE_FILTER" <<'PY'
import json, pathlib, sys, datetime
from collections import Counter, defaultdict

root = pathlib.Path(sys.argv[1])
limit = int(sys.argv[2] or 0)
type_filter = (sys.argv[3] or "").strip() or None

sessions = []
for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
    if not d.is_dir() or d.name.startswith("_"):
        continue
    metrics_path = d / "metrics.json"
    if not metrics_path.exists():
        continue
    try:
        m = json.loads(metrics_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    m["_dir"] = d.name
    m.setdefault("review_type", "implementation")
    if type_filter and m["review_type"] != type_filter:
        continue
    sessions.append(m)
    if limit and len(sessions) >= limit:
        break

unique = Counter()
ver_c = ver_r = ver_i = 0
durations = []
task_calls = []
routes = Counter()
verdicts = Counter()
confidences = Counter()
review_types = Counter()
artifact_types = Counter()
packet_bytes = []
rematches = []

for m in sessions:
    review_types[m.get("review_type") or "implementation"] += 1
    if m.get("artifact_type"):
        artifact_types[m["artifact_type"]] += 1
    if m.get("packet_bytes") is not None:
        packet_bytes.append(int(m["packet_bytes"]))
    if m.get("rounds") is not None:
        rematches.append(max(0, int(m["rounds"]) - 1))
    for seat, n in (m.get("unique_findings_by_seat") or m.get("findings_by_seat") or {}).items():
        unique[seat] += int(n or 0)
    v = m.get("verification") or {}
    ver_c += int(v.get("confirmed") or 0)
    ver_r += int(v.get("rejected") or 0)
    ver_i += int(v.get("inconclusive") or 0)
    if m.get("duration_seconds") is not None:
        durations.append(int(m["duration_seconds"]))
    if m.get("task_calls") is not None:
        task_calls.append(int(m["task_calls"]))
    if m.get("risk"):
        routes[m["risk"]] += 1
    if m.get("verdict"):
        verdicts[m["verdict"]] += 1
    if m.get("engineering_confidence"):
        confidences[m["engineering_confidence"]] += 1

ver_total = ver_c + ver_r + ver_i
avg_dur = round(sum(durations) / len(durations), 1) if durations else None
avg_tasks = round(sum(task_calls) / len(task_calls), 2) if task_calls else None
medium_tasks = [m.get("task_calls") for m in sessions if m.get("risk") == "medium" and m.get("task_calls") is not None]
avg_medium = round(sum(medium_tasks) / len(medium_tasks), 2) if medium_tasks else None

avg_packet = round(sum(packet_bytes) / len(packet_bytes), 1) if packet_bytes else None
avg_rematch = round(sum(rematches) / len(rematches), 2) if rematches else None

rollup = {
    "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "policy": "learning_only_never_auto_tune",
    "sessions_counted": len(sessions),
    "type_filter": type_filter,
    "review_types": dict(review_types),
    "artifact_types": dict(artifact_types),
    "average_packet_bytes": avg_packet,
    "average_confirmation_rounds": avg_rematch,
    "unique_findings_by_seat": dict(unique),
    "verifier": {
        "confirmed": ver_c,
        "rejected": ver_r,
        "inconclusive": ver_i,
        "reject_rate_percent": round(100.0 * ver_r / ver_total, 1) if ver_total else None,
    },
    "average_duration_seconds": avg_dur,
    "average_task_calls": avg_tasks,
    "average_medium_route_task_calls": avg_medium,
    "routes": dict(routes),
    "verdicts": dict(verdicts),
    "engineering_confidence": dict(confidences),
    "session_ids": [m.get("session_id") or m.get("_dir") for m in sessions],
}

out_dir = root / "_rollup"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "metrics-rollup.json").write_text(json.dumps(rollup, indent=2) + "\n", encoding="utf-8")

def fmt_dur(sec):
    if sec is None:
        return "n/a"
    sec = int(sec)
    m, s = divmod(sec, 60)
    return f"{m}m {s}s"

md = f"""# Yonko metrics rollup

Generated: {rollup['generated_at']}
Sessions: {len(sessions)}{f" (filter: {type_filter})" if type_filter else ""}
Policy: learning only - never auto-tune

## Review types

- types: {dict(review_types)}
- artifacts: {dict(artifact_types) or 'none'}

## Unique findings by seat (sum)

{chr(10).join(f'- {k}: {v}' for k, v in sorted(unique.items())) or '- none'}

## Verifier

- confirmed: {ver_c}
- rejected: {ver_r}
- inconclusive: {ver_i}
- reject rate: {rollup['verifier']['reject_rate_percent']}%

## Averages

- review duration: {fmt_dur(avg_dur)}
- task calls (all): {avg_tasks}
- task calls (medium route): {avg_medium}
- packet bytes: {avg_packet}
- confirmation rounds: {avg_rematch}

## Routes / verdicts / confidence

- routes: {dict(routes)}
- verdicts: {dict(verdicts)}
- confidence: {dict(confidences)}
"""
(out_dir / "metrics-rollup.md").write_text(md, encoding="utf-8")
print(json.dumps({"ok": True, "sessions": len(sessions), "out": str(out_dir / "metrics-rollup.json")}, indent=2))
PY
