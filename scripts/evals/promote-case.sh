#!/usr/bin/env bash
# promote-case.sh - human-gated promotion of eval candidate → eval case.
# Required: --approved-by, --confirm-hash (= packet.meta.json packet_hash), secret scan.
# Exit codes: 0 ok; 2 missing args; 3 hash mismatch/empty; 4 secret scan; 5 exists; 6 no candidate
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../_common.sh
source "$SCRIPT_DIR/../_common.sh"

SESSION=""
APPROVED_BY=""
CONFIRM_HASH=""
CASE_ID=""
OVERWRITE=0
REASON=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --approved-by) APPROVED_BY="${2:-}"; shift 2 ;;
    --confirm-hash) CONFIRM_HASH="${2:-}"; shift 2 ;;
    --case-id) CASE_ID="${2:-}"; shift 2 ;;
    --reason) REASON="${2:-}"; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    -h|--help)
      echo "Usage: promote-case.sh --session DIR --approved-by NAME --confirm-hash HASH [--case-id ID] [--overwrite]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

[[ -n "$SESSION" && -n "$APPROVED_BY" && -n "$CONFIRM_HASH" ]] || {
  echo '{"ok":false,"error":"missing_required_args","need":["--session","--approved-by","--confirm-hash"]}' >&2
  exit 2
}
[[ -d "$SESSION" ]] || yonko_die "session not found: $SESSION"

python3 - "$SESSION" "$APPROVED_BY" "$CONFIRM_HASH" "$CASE_ID" "$OVERWRITE" "$REASON" "$SKILL_ROOT" <<'PY'
import json, sys, hashlib, datetime
from pathlib import Path

session = Path(sys.argv[1]).resolve()
approved_by = sys.argv[2]
confirm_hash = (sys.argv[3] or "").strip().lower()
case_id = (sys.argv[4] or "").strip()
overwrite = sys.argv[5] == "1"
reason = sys.argv[6] or "human_promoted"
skill = Path(sys.argv[7])

sys.path.insert(0, str(skill / "scripts"))
from lib.evaluation.config import secret_scan_text, skill_root
from lib.evaluation.facts import load_json
from lib.evaluation.io import write_json

# Refuse forbidden auto-promote / CI-gate YAML keys if present (comments ignored).
import re
ev = (skill / "config" / "evaluation.yaml").read_text(encoding="utf-8")
if re.search(r"(?m)^\s*promote_automatically\s*:", ev) or re.search(r"(?m)^\s*ci_gate\s*:", ev):
    print(json.dumps({"ok": False, "error": "forbidden_config_keys_present"}))
    sys.exit(1)

meta = load_json(session / "packet.meta.json") or {}
measurement = load_json(session / "evaluation" / "review-measurement.json") or {}
candidate = load_json(session / "evaluation" / "eval-candidate.json")
packet_hash = (
    (meta.get("packet_hash") if isinstance(meta, dict) else None)
    or measurement.get("packet_hash")
    or ""
)
packet_hash = str(packet_hash).strip().lower()

if not packet_hash:
    print(json.dumps({"ok": False, "error": "empty_packet_hash", "exit_hint": 3}))
    sys.exit(3)
if not confirm_hash:
    print(json.dumps({"ok": False, "error": "empty_confirm_hash", "exit_hint": 3}))
    sys.exit(3)
if confirm_hash != packet_hash:
    print(json.dumps({
        "ok": False,
        "error": "hash_mismatch",
        "expected_prefix": packet_hash[:16],
        "got_prefix": confirm_hash[:16],
        "exit_hint": 3,
    }))
    sys.exit(3)

if candidate is None:
    print(json.dumps({"ok": False, "error": "no_eval_candidate", "exit_hint": 6}))
    sys.exit(6)

# Secret scan over session artefacts that would be copied
scan_targets = []
for rel in (
    "packet.md",
    "packet.meta.json",
    "findings.json",
    "evaluation/review-measurement.json",
    "session.json",
):
    p = session / rel
    if p.is_file():
        scan_targets.append(p.read_text(encoding="utf-8", errors="replace"))
hits = []
for t in scan_targets:
    hits.extend(secret_scan_text(t))
if hits:
    print(json.dumps({"ok": False, "error": "secret_scan_failed", "hits": hits[:5], "exit_hint": 4}))
    sys.exit(4)

sid = measurement.get("session_id") or session.name
if not case_id:
    case_id = f"case-{sid}"

cases_dir = skill / "evals" / "cases"
cases_dir.mkdir(parents=True, exist_ok=True)
case_path = cases_dir / f"{case_id}.json"
if case_path.exists() and not overwrite:
    print(json.dumps({"ok": False, "error": "case_exists", "case_id": case_id, "exit_hint": 5}))
    sys.exit(5)

case = {
    "schema_version": 1,
    "case_id": case_id,
    "source_session_id": sid,
    "source_session_path": str(session),
    "packet_hash": packet_hash,
    "creation_reason": reason,
    "approved_by": approved_by,
    "promoted_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "candidate_reasons": candidate.get("reasons") if isinstance(candidate, dict) else [],
    "review_type": measurement.get("review_type"),
}
write_json(case_path, case)

# Mark candidate promoted=false still (case exists; candidate never auto-promotes)
# Record promotion receipt beside candidate
receipt = {
    "schema_version": 1,
    "case_id": case_id,
    "approved_by": approved_by,
    "confirm_hash": packet_hash,
    "promoted_at": case["promoted_at"],
}
write_json(session / "evaluation" / "promotion-receipt.json", receipt)

manifest = skill / "evals" / "manifests" / "cases.jsonl"
manifest.parent.mkdir(parents=True, exist_ok=True)
with manifest.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"case_id": case_id, "packet_hash": packet_hash, "approved_by": approved_by}) + "\n")

print(json.dumps({"ok": True, "case_id": case_id, "path": str(case_path), "packet_hash": packet_hash}, indent=2))
PY
EC=$?
exit "$EC"
