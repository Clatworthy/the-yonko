#!/usr/bin/env bash
# init-session.sh - create consistent machine-readable session scaffolding.
# Usage: init-session.sh [--id <slug>] [--repo <label>] [--branch <name>] [--mode standard|autopilot]
#                        [--type implementation|plan|document] [--artifact pap|prd|adr|design]
#                        [--doc-mode create|review] [--linked-session ID_OR_PATH]
# Prints session directory path on stdout.
#
# --type defaults to implementation so existing V2 callers behave identically.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

ID=""
REPO=""
BRANCH=""
MODE="standard"
FORCE_ROUTE=""
REVIEW_TYPE="implementation"
ARTIFACT=""
DOC_MODE=""
LINKED_SESSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --id) ID="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --force-route) FORCE_ROUTE="${2:-}"; shift 2 ;;
    --type) REVIEW_TYPE="${2:-}"; shift 2 ;;
    --artifact) ARTIFACT="${2:-}"; shift 2 ;;
    --doc-mode) DOC_MODE="${2:-}"; shift 2 ;;
    --linked-session) LINKED_SESSION="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: init-session.sh [--id slug] [--repo label] [--branch name] [--mode standard|autopilot] [--force-route trivial|low|medium|high|critical|quick|full|review] [--type implementation|plan|document] [--artifact pap|prd|adr|design] [--doc-mode create|review] [--linked-session ID]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

[[ "$MODE" == "standard" || "$MODE" == "autopilot" ]] || yonko_die "mode must be standard|autopilot"
case "$REVIEW_TYPE" in
  implementation|plan|document) ;;
  *) yonko_die "--type must be implementation|plan|document" ;;
esac
if [[ "$REVIEW_TYPE" == "document" ]]; then
  case "$ARTIFACT" in
    pap|prd|adr|design) ;;
    *) yonko_die "document sessions require --artifact pap|prd|adr|design" ;;
  esac
  [[ -z "$DOC_MODE" ]] && DOC_MODE="review"
  case "$DOC_MODE" in
    create|review) ;;
    *) yonko_die "--doc-mode must be create|review" ;;
  esac
else
  [[ -z "$ARTIFACT" ]] || yonko_die "--artifact only valid for --type document"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "$ID" ]]; then
  case "$REVIEW_TYPE" in
    plan) ID="plan-${STAMP}" ;;
    document) ID="doc-${ARTIFACT}-${STAMP}" ;;
    *) ID="session-${STAMP}" ;;
  esac
fi

SESSION_DIR="$YONKO_SESSIONS_ROOT/$ID"
mkdir -p "$SESSION_DIR"

# Atomic-ish create: refuse overwrite of existing session.json
if [[ -f "$SESSION_DIR/session.json" ]]; then
  yonko_die "session already exists: $SESSION_DIR (choose a new --id)"
fi

python3 - "$SESSION_DIR" "$ID" "$STAMP" "$MODE" "$REPO" "$BRANCH" "$FORCE_ROUTE" "$REVIEW_TYPE" "$ARTIFACT" "$DOC_MODE" "$LINKED_SESSION" <<'PY'
import json, sys, pathlib
(session_dir, sid, stamp, mode, repo, branch, force_route,
 review_type, artifact, doc_mode, linked) = sys.argv[1:12]

linked_ref = None
if linked:
    p = pathlib.Path(linked)
    if not p.exists():
        candidate = pathlib.Path(session_dir).parent / linked
        p = candidate if candidate.exists() else None
    if p is None:
        raise SystemExit(f"yonko: --linked-session not found: {linked}")
    linked_ref = str(p)

doc = {
  "version": "3.0.0",
  "session_id": sid,
  "started_at": stamp,
  "review_type": review_type,
  "artifact_type": artifact or None,
  "document_mode": doc_mode or None,
  "linked_session": linked_ref,
  "mode": mode,
  "repo": repo or None,
  "branch": branch or None,
  "force_route": force_route or None,
  "risk": None,
  "risk_basis": "diff-derived" if review_type == "implementation" else "heuristic from stated scope and inspected context",
  "risk_reasons": [],
  "round": 0,
  "packet_hash": None,
  "packet_version": 0,
  "status": "initialized",
  "subagent_calls": 0,
  "applied_titles": [],
  "thrash_count": {},
}
path = pathlib.Path(session_dir) / "session.json"
path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
(pathlib.Path(session_dir) / "events.jsonl").touch()

label = review_type if not artifact else f"{review_type}:{artifact}"
(pathlib.Path(session_dir) / "bulletins.md").write_text(
    f"# Yonko bulletins\n\n- Session: {sid}\n- Started: {stamp}\n- Type: {label}\n- Mode: {mode}\n",
    encoding="utf-8",
)

if review_type == "implementation":
    rule = "- Rule: Yonko advise; Chair applies; human alone commits\n"
else:
    rule = ("- Rule: Yonko advise; Chair revises the artifact only; no production code changes\n"
            "- Rule: no automatic continuation into implementation or publication\n")
(pathlib.Path(session_dir) / "CHANGELOG.md").write_text(
    f"# Yonko session\n\n- Session: {sid}\n- Started: {stamp}\n- Type: {label}\n- Mode: {mode}\n"
    f"- Repo: {repo or 'n/a'}\n- Branch: {branch or 'n/a'}\n"
    f"- Linked session: {linked_ref or 'n/a'}\n"
    + rule +
    "- Soft stop: Pass / Deadlock / Adjourned\n",
    encoding="utf-8",
)
PY

# Freeze active execution profile into the session (reproducibility).
# Missing marker -> cursor-standard. Marker changes later do not mutate this freeze.
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<PY || yonko_info "execution profile freeze skipped (non-fatal)"
import sys
from pathlib import Path
sys.path.insert(0, "$SCRIPT_DIR")
from lib.runtime.resolve_profile import freeze_profile_into_session, ProfileError
try:
    freeze = freeze_profile_into_session(Path("$SESSION_DIR"))
    print(f"yonko: execution profile frozen -> {freeze.get('executionProfile')}", file=sys.stderr)
except ProfileError as e:
    print(f"yonko: execution profile freeze failed: {e.message}", file=sys.stderr)
    raise SystemExit(1)
PY

yonko_info "initialized $SESSION_DIR"
INIT_DATA="$(python3 -c "
import json,sys
print(json.dumps({'mode': sys.argv[1], 'review_type': sys.argv[2], 'artifact_type': sys.argv[3] or None, 'document_mode': sys.argv[4] or None, 'linked_session': sys.argv[5] or None}))
" "$MODE" "$REVIEW_TYPE" "$ARTIFACT" "$DOC_MODE" "$LINKED_SESSION")"
"$SCRIPT_DIR/record-event.sh" --session "$SESSION_DIR" --type session_initialized --data "$INIT_DATA" || true
# stdout must be ONLY the session path (for $(init-session.sh) capture)
printf '%s\n' "$SESSION_DIR"
