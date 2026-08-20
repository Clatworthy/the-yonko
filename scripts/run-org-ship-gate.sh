#!/usr/bin/env bash
# run-org-ship-gate.sh - optional post-council hostile org ship gate via OpenCode Go
#
# Model: opencode-go/gpt-5.6-luna (OpenAI family on OpenCode Go; not the
# standalone OpenAI CLI path).
# After Yonko seats are Content, Chair runs this before finalize --verdict pass
# when the matched adapter enables org_ship_gate.
#
# Usage:
#   run-org-ship-gate.sh --session DIR [--workspace ROOT] [--model ID] [--export-only] [--validate-only]
#   run-org-ship-gate.sh --session DIR --import-result PATH

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
WORKSPACE="${YONKO_PROJECT_ROOT:-$(pwd)}"
MODEL="opencode-go/gpt-5.6-luna"
EXPORT_ONLY=0
VALIDATE_ONLY=0
IMPORT_RESULT=""
TIMEOUT_SEC=900

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --timeout-sec) TIMEOUT_SEC="${2:-}"; shift 2 ;;
    --export-only) EXPORT_ONLY=1; shift ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
    --import-result) IMPORT_RESULT="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: run-org-ship-gate.sh --session DIR [--workspace ROOT] [--model opencode-go/gpt-5.6-luna]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
SESSION="$(cd "$SESSION" && pwd)"
GATE_DIR="$SESSION/org-ship-gate"
mkdir -p "$GATE_DIR"

if [[ "$VALIDATE_ONLY" -eq 1 ]]; then
  python3 "$SCRIPT_DIR/lib/org_ship_gate.py" --session "$SESSION" --json
  exit $?
fi

if [[ -n "$IMPORT_RESULT" ]]; then
  cp "$IMPORT_RESULT" "$GATE_DIR/result.json"
  python3 "$SCRIPT_DIR/lib/org_ship_gate.py" --session "$SESSION" --json
  exit $?
fi

ARGS=(
  --session "$SESSION"
  --workspace "$WORKSPACE"
  --model "$MODEL"
  --timeout-sec "$TIMEOUT_SEC"
)
if [[ "$EXPORT_ONLY" -eq 1 ]]; then
  ARGS+=(--export-only)
fi

set +e
python3 "$SCRIPT_DIR/lib/run_org_ship_gate_opencode.py" "${ARGS[@]}"
GATE_RC=$?
set -e

if [[ -x "$SCRIPT_DIR/record-event.sh" ]]; then
  "$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type org_ship_gate \
    --data "$(python3 -c "import json;print(json.dumps({'ok': $([[ $GATE_RC -eq 0 ]] && echo true || echo false), 'model': '$MODEL', 'runtime': 'opencode-go'}))")" \
    || true
fi

exit "$GATE_RC"
