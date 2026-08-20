#!/usr/bin/env bash
# route-reviewers.sh - deterministic seat selection from change-classes + risk + routing policy.
# Implementation review only.
# Usage:
#   route-reviewers.sh --session <dir>
# Requires: evidence/risk.json, evidence/change-classes.json
# Writes evidence/routing.json. Prints JSON on stdout.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: route-reviewers.sh --session DIR"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
REVIEW_TYPE="$(python3 -c "import json,pathlib,sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text()).get('review_type') or 'implementation')" "$SESSION/session.json")"
[[ "$REVIEW_TYPE" == "implementation" ]] || yonko_die "route-reviewers.sh is implementation-only"
[[ -f "$SESSION/evidence/risk.json" ]] || yonko_die "run classify-risk.sh first"
[[ -f "$SESSION/evidence/change-classes.json" ]] || yonko_die "run classify-change.sh first"
[[ -f "$YONKO_CONFIG/routing-policy.yaml" ]] || yonko_die "missing config/routing-policy.yaml"

python3 - "$SESSION" "$YONKO_CONFIG" "$SCRIPT_DIR/lib" <<'PY'
import json, os, pathlib, sys

session_dir = pathlib.Path(sys.argv[1])
config_dir = pathlib.Path(sys.argv[2])
sys.path.insert(0, sys.argv[3])

import routing  # noqa: E402

policy = routing.load_policy_pair(config_dir)
risk = json.loads((session_dir / "evidence" / "risk.json").read_text(encoding="utf-8"))
classes = json.loads((session_dir / "evidence" / "change-classes.json").read_text(encoding="utf-8"))
project_root = os.environ.get("YONKO_PROJECT_ROOT")
luffy_ok = routing.luffy_available(config_dir, project_root)
out = routing.route_reviewers(classes, risk, policy, luffy_ok=luffy_ok)
path = session_dir / "evidence" / "routing.json"
path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2, sort_keys=True))
sys.stderr.write(routing.explain_routing(out))
event = {
    "seats": out.get("seats"),
    "require_verifier": out.get("require_verifier"),
    "band": out.get("risk_band"),
    "classes": out.get("classes_applied"),
}
(session_dir / "evidence" / ".reviewers-routed-event.json").write_text(
    json.dumps(event), encoding="utf-8"
)
PY

"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type reviewers_routed \
  --data "$(cat "$SESSION/evidence/.reviewers-routed-event.json")"
rm -f "$SESSION/evidence/.reviewers-routed-event.json"
yonko_info "routing: $SESSION/evidence/routing.json"
