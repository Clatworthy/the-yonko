#!/usr/bin/env bash
# seat-council.sh - prepare / status / require-complete / kickoff / execute-awaiting
#
# Usage:
#   seat-council.sh --session DIR --prepare [--with-kickoff]
#   seat-council.sh --session DIR --kickoff
#   seat-council.sh --session DIR --status
#   seat-council.sh --session DIR --require-complete
#   seat-council.sh --session DIR --execute-awaiting
#
# --prepare: dispatch-only invoke for every routing.json seat; write council.json
# --with-kickoff: with --prepare, also detach OpenCode --execute immediately (durable start)
# --kickoff: detach parallel --execute for never-started OpenCode seats (parent start)
# --status: print per-seat completed / awaiting / missing
# --require-complete: exit non-zero if any OpenCode seat still incomplete
# --execute-awaiting: blocking parallel --execute for never-started seats (watchdog)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
MODE=""
WITH_KICKOFF=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --prepare|--status|--require-complete|--execute-awaiting|--kickoff) MODE="${1#--}"; shift ;;
    --with-kickoff) WITH_KICKOFF=1; shift ;;
    -h|--help)
      echo "Usage: seat-council.sh --session DIR --prepare [--with-kickoff]|--kickoff|--status|--require-complete|--execute-awaiting"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -n "$MODE" ]] || yonko_die "pass --prepare, --kickoff, --status, --require-complete, or --execute-awaiting"
SESSION="$(cd "$SESSION" && pwd)"

if [[ "$MODE" == "prepare" && "$WITH_KICKOFF" -eq 1 ]]; then
  EXTRA_ARGS+=(--with-kickoff)
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m lib.runtime.seat_council --session "$SESSION" "--$MODE" "${EXTRA_ARGS[@]}"
