#!/usr/bin/env bash
# sanitise-and-hash-packet.sh - build packet.md from docket + evidence, secret-scan, hash, version.
# Usage:
#   sanitise-and-hash-packet.sh --session <dir> --docket <docket.md>
#
# Assembly is review-type aware (read from session.json):
#   implementation -> docket + repos + diff map + full diffs   (V2 layout, unchanged)
#   plan           -> docket + repos named + plan + sources + recon
#   document       -> docket + section map + draft (review mode) + sources + recon
#
# Writes: packet.md, packet.meta.json; updates session.json packet_hash/version.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
DOCKET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --docket) DOCKET="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: sanitise-and-hash-packet.sh --session DIR --docket FILE"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -n "$DOCKET" && -f "$DOCKET" ]] || yonko_die "docket file required"

REVIEW_TYPE="$(python3 -c "import json,pathlib,sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text()).get('review_type') or 'implementation')" "$SESSION/session.json")"

case "$REVIEW_TYPE" in
  implementation)
    [[ -f "$SESSION/evidence/repos.json" ]] || yonko_die "run collect-evidence.sh first" ;;
  plan)
    [[ -f "$SESSION/evidence/plan-refs.json" ]] || yonko_die "run collect-plan-evidence.sh first" ;;
  document)
    [[ -f "$SESSION/evidence/doc-refs.json" ]] || yonko_die "run collect-document-evidence.sh first" ;;
  *) yonko_die "unknown review_type in session.json: $REVIEW_TYPE" ;;
esac

python3 "$SCRIPT_DIR/lib/assemble_packet.py" "$SESSION" "$DOCKET" "$REVIEW_TYPE"

META_DATA="$(python3 -c "import json,pathlib; m=json.loads(pathlib.Path('$SESSION/packet.meta.json').read_text()); print(json.dumps({'packet_hash': m['packet_hash'], 'packet_version': m['packet_version'], 'bytes': m['bytes'], 'review_type': m.get('review_type')}))")"
"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type packet_hashed --data "$META_DATA"
yonko_info "packet ready ($REVIEW_TYPE): $SESSION/packet.md"
