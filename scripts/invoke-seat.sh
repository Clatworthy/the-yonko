#!/usr/bin/env bash
# invoke-seat.sh - provider-neutral seat dispatch
# Usage:
#   invoke-seat.sh --session DIR --seat SEAT [--workdir PATH]
#                  [--execute]   # OpenCode: run CLI (from Cursor Task wrapper)
#                  [--runtime NAME] [--model ID]   # optional; must match freeze
#
# Default for OpenCode seats: write Cursor Task dispatch only (named tile UX).
# Wrapper Task must re-invoke with --execute to run OpenCode.
# Runtime and model come from the session-frozen execution profile.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
SEAT=""
WORKDIR=""
RUNTIME=""
MODEL=""
EXECUTE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --seat) SEAT="${2:-}"; shift 2 ;;
    --workdir) WORKDIR="${2:-}"; shift 2 ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    --packet|--schema|--output) shift 2 ;; # accepted for CLI symmetry; session freeze wins
    -h|--help)
      echo "Usage: invoke-seat.sh --session DIR --seat SEAT [--workdir DIR] [--execute]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -n "$SEAT" ]] || yonko_die "--seat required"
SESSION="$(cd "$SESSION" && pwd)"

if [[ -n "$RUNTIME" || -n "$MODEL" ]]; then
  RUNTIME="$RUNTIME" MODEL="$MODEL" SESSION="$SESSION" SEAT="$SEAT" python3 - <<'PY'
import json, os, sys
from pathlib import Path
session = os.environ["SESSION"]
seat = os.environ["SEAT"]
runtime = os.environ.get("RUNTIME") or ""
model = os.environ.get("MODEL") or ""
sess = json.loads(Path(session, "session.json").read_text(encoding="utf-8"))
freeze = sess.get("execution_profile") or {}
if not freeze.get("frozen"):
    raise SystemExit("yonko: session has no frozen execution_profile yet")
row = next((r for r in freeze.get("seats") or [] if r.get("seat") == seat), None)
if not row:
    raise SystemExit(f"yonko: seat {seat} missing from freeze")
if runtime and row.get("runtime") != runtime:
    raise SystemExit(f"yonko: --runtime {runtime} != frozen {row.get('runtime')}")
if model and row.get("model") != model and not str(row.get("model", "")).startswith("unresolved:"):
    raise SystemExit(f"yonko: --model {model} != frozen {row.get('model')}")
PY
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
ARGS=(--session "$SESSION" --seat "$SEAT" --json)
if [[ -n "$WORKDIR" ]]; then
  ARGS+=(--workdir "$WORKDIR")
fi
if [[ "$EXECUTE" -eq 1 ]]; then
  ARGS+=(--execute)
fi
exec python3 -m lib.runtime.invoke_seat "${ARGS[@]}"
