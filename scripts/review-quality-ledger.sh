#!/usr/bin/env bash
# review-quality-ledger.sh - observational quality ledger for real Yonko reviews.
# Learning only. Never feeds seating, routing, adjudication, or apply.
#
# Usage:
#   review-quality-ledger.sh --session DIR --record
#   review-quality-ledger.sh --session DIR --annotate \
#     [--reached-prod yes|no|unknown] [--human-missed yes|no|unknown] [--notes TEXT] \
#     [--finding-id ID --finding-reached-prod … --finding-human-missed …]
#   review-quality-ledger.sh --rollup
#
# Writes:
#   SESSION/review-quality.json
#   ~/.cursor/yonko-sessions/_rollup/review-quality-ledger.jsonl
#   ~/.cursor/yonko-sessions/_rollup/review-quality-rollup.{json,md}

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
MODE=""
REACHED=""
MISSED=""
NOTES=""
FINDING_ID=""
FINDING_REACHED=""
FINDING_MISSED=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --record|--annotate|--rollup) MODE="${1#--}"; shift ;;
    --reached-prod) REACHED="${2:-}"; shift 2 ;;
    --human-missed) MISSED="${2:-}"; shift 2 ;;
    --notes) NOTES="${2:-}"; shift 2 ;;
    --finding-id) FINDING_ID="${2:-}"; shift 2 ;;
    --finding-reached-prod) FINDING_REACHED="${2:-}"; shift 2 ;;
    --finding-human-missed) FINDING_MISSED="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '1,20p' "$0" | tail -n +2
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

[[ -n "$MODE" ]] || yonko_die "pass --record, --annotate, or --rollup"

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export YONKO_SESSIONS_ROOT="${YONKO_SESSIONS_ROOT:-$HOME/.cursor/yonko-sessions}"

if [[ "$MODE" == "rollup" ]]; then
  python3 - <<'PY'
import json, os
from pathlib import Path
from lib.review_quality_ledger import write_rollup, ledger_path
root = Path(os.environ["YONKO_SESSIONS_ROOT"])
rollup = write_rollup(root)
print(json.dumps({"ok": True, "sessions": rollup["sessions"], "ledger": str(ledger_path(root)), "rollup": str(root / "_rollup" / "review-quality-rollup.json")}, indent=2))
PY
  exit 0
fi

yonko_require_session "$SESSION"
SESSION="$(cd "$SESSION" && pwd)"

if [[ "$MODE" == "annotate" ]]; then
  export YONKO_RQ_REACHED="$REACHED"
  export YONKO_RQ_MISSED="$MISSED"
  export YONKO_RQ_NOTES="$NOTES"
  export YONKO_RQ_FINDING_ID="$FINDING_ID"
  export YONKO_RQ_FINDING_REACHED="$FINDING_REACHED"
  export YONKO_RQ_FINDING_MISSED="$FINDING_MISSED"
  python3 - "$SESSION" <<'PY'
import json, os, sys
from pathlib import Path
from lib.review_quality_ledger import annotate_human, build_row, upsert_row, write_rollup
session = Path(sys.argv[1])
annotate_human(
    session,
    reached_production=os.environ.get("YONKO_RQ_REACHED") or None,
    reviewer_found_human_missed=os.environ.get("YONKO_RQ_MISSED") or None,
    notes=os.environ.get("YONKO_RQ_NOTES") or None,
    finding_id=os.environ.get("YONKO_RQ_FINDING_ID") or None,
    finding_reached_production=os.environ.get("YONKO_RQ_FINDING_REACHED") or None,
    finding_human_missed=os.environ.get("YONKO_RQ_FINDING_MISSED") or None,
)
root = Path(os.environ["YONKO_SESSIONS_ROOT"])
row = build_row(session)
path = upsert_row(root, row)
write_rollup(root)
print(json.dumps({"ok": True, "session_id": row["session_id"], "ledger": str(path), "human": row["human"]}, indent=2))
PY
  exit 0
fi

# --record
python3 - "$SESSION" <<'PY'
import json, os, sys
from pathlib import Path
from lib.review_quality_ledger import build_row, upsert_row, write_rollup
session = Path(sys.argv[1])
root = Path(os.environ["YONKO_SESSIONS_ROOT"])
row = build_row(session)
path = upsert_row(root, row)
write_rollup(root)
print(json.dumps({
  "ok": True,
  "session_id": row["session_id"],
  "ledger": str(path),
  "review_quality": str(session / "review-quality.json"),
  "gaps": row.get("gaps"),
  "cost_usd": row.get("cost", {}).get("total_opencode_usd"),
  "duplicates": row.get("findings", {}).get("duplicate_cross_seat_count"),
}, indent=2))
PY
