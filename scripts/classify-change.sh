#!/usr/bin/env bash
# classify-change.sh - deterministic change classes from evidence + routing-policy signals.
# Implementation review only.
# Usage:
#   classify-change.sh --session <dir> [--advisory class1,class2]
# Writes evidence/change-classes.json. Prints JSON on stdout.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
ADVISORY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --advisory) ADVISORY="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: classify-change.sh --session DIR [--advisory class1,class2]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
REVIEW_TYPE="$(python3 -c "import json,pathlib,sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text()).get('review_type') or 'implementation')" "$SESSION/session.json")"
[[ "$REVIEW_TYPE" == "implementation" ]] || yonko_die "classify-change.sh is implementation-only"
[[ -f "$SESSION/evidence/repos.json" ]] || yonko_die "run collect-evidence.sh first"
[[ -f "$YONKO_CONFIG/routing-policy.yaml" ]] || yonko_die "missing config/routing-policy.yaml"

python3 - "$SESSION" "$YONKO_CONFIG" "$ADVISORY" "$SCRIPT_DIR/lib" <<'PY'
import json, pathlib, sys

session_dir = pathlib.Path(sys.argv[1])
config_dir = pathlib.Path(sys.argv[2])
advisory_raw = (sys.argv[3] or "").strip()
sys.path.insert(0, sys.argv[4])

import routing  # noqa: E402

policy = routing.load_policy_pair(config_dir)
advisory = [x.strip() for x in advisory_raw.split(",") if x.strip()] if advisory_raw else []
out = routing.classify_change(session_dir, policy, advisory=advisory)
path = session_dir / "evidence" / "change-classes.json"
path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2, sort_keys=True))
event = {
    "classes": out.get("classes"),
    "advisory": out.get("advisory_classes"),
    "dropped": out.get("dropped_advisory"),
}
(session_dir / "evidence" / ".change-classified-event.json").write_text(
    json.dumps(event), encoding="utf-8"
)
PY

"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type change_classified \
  --data "$(cat "$SESSION/evidence/.change-classified-event.json")"
rm -f "$SESSION/evidence/.change-classified-event.json"
yonko_info "change classes: $SESSION/evidence/change-classes.json"
