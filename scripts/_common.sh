#!/usr/bin/env bash
# Shared helpers for Yonko V2 scripts.
# shellcheck shell=bash

set -euo pipefail

YONKO_SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YONKO_SESSIONS_ROOT="${YONKO_SESSIONS_ROOT:-$HOME/.cursor/yonko-sessions}"
YONKO_CONTRACTS="$YONKO_SKILL_ROOT/contracts"
YONKO_CONFIG="$YONKO_SKILL_ROOT/config"

yonko_die() {
  echo "yonko: ERROR: $*" >&2
  exit 1
}

yonko_info() {
  echo "yonko: $*" >&2
}

yonko_require_session() {
  local session_dir="${1:-}"
  [[ -n "$session_dir" ]] || yonko_die "session dir required"
  [[ -d "$session_dir" ]] || yonko_die "session dir not found: $session_dir"
  [[ -f "$session_dir/session.json" ]] || yonko_die "missing session.json in $session_dir"
}

yonko_sha256() {
  local f="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$f" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$f" | awk '{print $1}'
  else
    python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$f"
  fi
}


# V3.4 workflow observe. Shadow: always return 0. Enforce: propagate non-zero on blocked.
# Usage: yonko_workflow_observe <session_dir> <transition> [json_data] [idempotency_key]
yonko_workflow_observe() {
  local session_dir="${1:-}"
  local transition="${2:-}"
  local data="${3:-{}}"
  local idem="${4:-}"
  [[ -n "$session_dir" && -n "$transition" ]] || return 0
  local _here wf_py rc out
  _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  wf_py="$_here/workflow/transition.py"
  [[ -f "$wf_py" ]] || return 0
  if [[ -n "$idem" ]]; then
    out="$(python3 "$wf_py" --session "$session_dir" --transition "$transition" --data "$data" --idempotency-key "$idem" 2>/dev/null)" && rc=$? || rc=$?
  else
    out="$(python3 "$wf_py" --session "$session_dir" --transition "$transition" --data "$data" 2>/dev/null)" && rc=$? || rc=$?
  fi
  # Implementation/reporting failures are fail-open (transition.py returns 0 with fail_open)
  if [[ "$rc" -ne 0 ]]; then
    return "$rc"
  fi
  return 0
}
