#!/usr/bin/env bash
# record-cursor-seat.sh - mark Cursor Task seat complete; record duration_ms
# Usage:
#   record-cursor-seat.sh --session DIR --seat SEAT [--model-actual ID]
#                         [--schema-valid|--schema-invalid] [--incomplete]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
SEAT=""
MODEL_ACTUAL=""
SCHEMA_MODE="auto"
COMPLETED=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --seat) SEAT="${2:-}"; shift 2 ;;
    --model-actual) MODEL_ACTUAL="${2:-}"; shift 2 ;;
    --schema-valid) SCHEMA_MODE="valid"; shift ;;
    --schema-invalid) SCHEMA_MODE="invalid"; shift ;;
    --incomplete) COMPLETED=0; shift ;;
    -h|--help)
      echo "Usage: record-cursor-seat.sh --session DIR --seat SEAT [--model-actual ID] [--schema-valid|--schema-invalid] [--incomplete]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -n "$SEAT" ]] || yonko_die "--seat required"
SESSION="$(cd "$SESSION" && pwd)"

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export YONKO_SCRIPTS_DIR="$SCRIPT_DIR"
SESSION="$SESSION" SEAT="$SEAT" MODEL_ACTUAL="$MODEL_ACTUAL" \
SCHEMA_MODE="$SCHEMA_MODE" COMPLETED="$COMPLETED" python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
from lib.runtime.cursor_adapter import record_cursor_completion

session = Path(os.environ["SESSION"])
seat = os.environ["SEAT"]
model = (os.environ.get("MODEL_ACTUAL") or "").strip() or None
mode = os.environ.get("SCHEMA_MODE") or "auto"
completed = os.environ.get("COMPLETED") != "0"
out = session / "runtime" / seat / "findings.json"

if mode == "valid":
    schema_valid = True
elif mode == "invalid":
    schema_valid = False
else:
    schema_valid = False
    if out.is_file():
        validate = Path(os.environ["YONKO_SCRIPTS_DIR"]) / "validate-artifact.sh"
        if validate.is_file():
            proc = subprocess.run(
                ["bash", str(validate), "--kind", "findings", "--file", str(out)],
                capture_output=True,
                text=True,
                check=False,
            )
            schema_valid = proc.returncode == 0

result = record_cursor_completion(
    session,
    seat,
    model_actual=model,
    output_path=str(out) if out.is_file() else None,
    schema_valid=schema_valid,
    completed=completed,
)
print(json.dumps(result, indent=2))
PY
