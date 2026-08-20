#!/usr/bin/env bash
# set-execution-profile.sh - switch the active execution profile marker
# Usage: set-execution-profile.sh --profile cursor-standard|cursor-opencode-go|cursor-max
# Does not mutate in-flight sessions (frozen profile stays).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

PROFILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: set-execution-profile.sh --profile cursor-standard|cursor-opencode-go|cursor-max"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

[[ -n "$PROFILE" ]] || yonko_die "--profile required"

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<PY
import json, sys
from pathlib import Path
sys.path.insert(0, "$SCRIPT_DIR")
from lib.runtime.resolve_profile import set_active_profile, ProfileError
try:
    doc = set_active_profile("$PROFILE")
except ProfileError as e:
    print(f"yonko: ERROR: {e.message}", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps(doc, indent=2))
print(f"yonko: active execution profile -> {doc['executionProfile']}", file=sys.stderr)
PY
