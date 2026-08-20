#!/usr/bin/env bash
# record-event.sh - append-only session event log + optional session.json field updates.
# Usage:
#   record-event.sh --session <dir> --type <event_type> [--data '<json object>']
#   record-event.sh --session <dir> --set-json '<json object merging into session.json>'
#
# Phase 0: also records shadow workflow transitions for known event types (fail-open).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
TYPE=""
DATA="{}"
SET_JSON=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --type) TYPE="${2:-}"; shift 2 ;;
    --data) DATA="${2:-}"; shift 2 ;;
    --set-json) SET_JSON="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: record-event.sh --session DIR (--type NAME [--data JSON] | --set-json JSON)"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
export YONKO_SCRIPTS_DIR="$SCRIPT_DIR"

python3 - "$SESSION" "$TYPE" "$DATA" "$SET_JSON" <<'PY'
import json, sys, pathlib, datetime, os, importlib.util
session_dir = pathlib.Path(sys.argv[1])
event_type = sys.argv[2]
data_raw = sys.argv[3]
set_raw = sys.argv[4]

session_path = session_dir / "session.json"
events_path = session_dir / "events.jsonl"

session = json.loads(session_path.read_text(encoding="utf-8"))

if set_raw:
    patch = json.loads(set_raw)
    if not isinstance(patch, dict):
        raise SystemExit("yonko: --set-json must be a JSON object")
    session.update(patch)
    session_path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

if event_type:
    try:
        data = json.loads(data_raw) if data_raw else {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"yonko: invalid --data JSON: {e}")
    if not isinstance(data, dict):
        raise SystemExit("yonko: --data must be a JSON object")
    event = {
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": event_type,
        "round": session.get("round", 0),
        "data": data,
    }
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")
    # V3.4 workflow: shadow would_block / enforce blocked (fail-closed on confirmed guards)
    wf_blocked = False
    wf_codes = []
    try:
        scripts = pathlib.Path(
            os.environ.get("YONKO_SCRIPTS_DIR")
            or str(pathlib.Path.home() / ".cursor/skills/the-yonko/scripts")
        )
        wf_dir = scripts / "workflow"
        if str(wf_dir) not in sys.path:
            sys.path.insert(0, str(wf_dir))
        wf_py = wf_dir / "transition.py"
        if wf_py.exists():
            import transition as mod  # noqa: E402
            mapping = {
                "session_initialized": "initialise",
                "evidence_collected": "collect_evidence",
                "plan_evidence_collected": "collect_evidence",
                "document_evidence_collected": "collect_evidence",
                "evidence_graph_built": "collect_evidence",
                "evidence_completeness_evaluated": "collect_evidence",
                "risk_classified": "classify_risk",
                "scope_risk_classified": "classify_risk",
                "packet_hashed": "pin_packet",
                "reviewers_seated": "seat_reviewers",
                "verification_completed": "verify",
                "artifact_revised": "apply_or_revise",
                "apply": "apply_or_revise",
                "scoped_verify": "scoped_verify",
                "session_finalized": "finalize",
            }
            # Map production apply flag for write fence
            if event_type == "apply":
                data = dict(data)
                data.setdefault("writes_production_code", True)
            tr = mapping.get(event_type)
            if tr:
                payload = dict(data)
                if tr == "seat_reviewers" and "count" in payload and "seat_count" not in payload:
                    payload["seat_count"] = payload.get("count")
                if tr == "apply_or_revise":
                    payload.setdefault("counts_as_confirmation", False)
                if tr == "finalize" and "verdict" not in payload:
                    payload["verdict"] = session.get("verdict")
                result = mod.record_transition(session_dir, tr, payload, None)
                if result.get("blocked"):
                    wf_blocked = True
                    wf_codes = result.get("failure_codes") or []
    except Exception:
        pass
    if wf_blocked:
        print(json.dumps({
            "ok": False,
            "blocked": True,
            "type": event_type,
            "failure_codes": wf_codes,
        }), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({"ok": True, "type": event_type}), file=sys.stderr)
elif set_raw:
    print(json.dumps({"ok": True, "session_updated": True}), file=sys.stderr)
else:
    raise SystemExit("yonko: provide --type and/or --set-json")
PY
