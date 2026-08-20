#!/usr/bin/env bash
# capture-evaluation.sh - re-run observational evaluation capture for a session.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: capture-evaluation.sh --session DIR"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done
[[ -n "$SESSION" ]] || yonko_die "--session required"
[[ -d "$SESSION" ]] || yonko_die "session dir not found: $SESSION"

python3 - "$SESSION" "$SCRIPT_DIR" <<'PY'
import json, sys
from pathlib import Path
session = Path(sys.argv[1]).resolve()
scripts = Path(sys.argv[2])
sys.path.insert(0, str(scripts))
from lib.evaluation.capture import capture_or_fail_open
from lib.review_quality_ledger import upsert_row, write_rollup
from lib.evaluation.config import sessions_root

cap = capture_or_fail_open(session)
if cap.get("ok") and cap.get("ledger_row"):
    root = sessions_root()
    upsert_row(root, cap["ledger_row"])
    write_rollup(root)
print(json.dumps(cap if not cap.get("measurement") else {
    "ok": cap.get("ok"),
    "fail_open": cap.get("fail_open"),
    "skipped": cap.get("skipped"),
    "error": cap.get("error"),
    "paths": cap.get("paths"),
    "session_id": (cap.get("measurement") or {}).get("session_id"),
    "adjudication_state": (cap.get("measurement") or {}).get("adjudication_state"),
}, indent=2))
if not cap.get("ok") and not cap.get("fail_open") and not cap.get("skipped"):
    sys.exit(1)
PY
