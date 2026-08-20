#!/usr/bin/env bash
# collect-plan-evidence.sh - evidence collector for PLAN review (V3).
# No diff required. Collects the plan document, optional ticket/source material,
# optional Chair reconnaissance notes, and repo context for named repositories.
#
# Usage:
#   collect-plan-evidence.sh --session DIR --plan FILE
#     [--source FILE]...            # ticket text, notes, spec excerpts
#     [--recon FILE]                # Chair reconnaissance notes (paths/symbols read)
#     [--repo ABS_PATH]...          # repositories the plan claims to touch
#
# Writes: evidence/plan.md, evidence/sources/, evidence/recon.md,
#         evidence/plan-refs.json, evidence/REPO_CONTEXT.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
PLAN=""
RECON=""
SOURCES=()
REPOS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --plan) PLAN="${2:-}"; shift 2 ;;
    --source) SOURCES+=("${2:-}"); shift 2 ;;
    --recon) RECON="${2:-}"; shift 2 ;;
    --repo) REPOS+=("${2:-}"); shift 2 ;;
    -h|--help)
      echo "Usage: collect-plan-evidence.sh --session DIR --plan FILE [--source FILE]... [--recon FILE] [--repo ABS]..."
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -n "$PLAN" && -f "$PLAN" ]] || yonko_die "--plan FILE required (the drafted implementation plan)"

EVID="$SESSION/evidence"
mkdir -p "$EVID/sources"

export YONKO_PLAN_SOURCES="$(
  if [[ ${#SOURCES[@]} -gt 0 ]]; then printf '%s\036' "${SOURCES[@]}"; fi
)"
export YONKO_PLAN_REPOS="$(
  if [[ ${#REPOS[@]} -gt 0 ]]; then printf '%s\036' "${REPOS[@]}"; fi
)"
export YONKO_PLAN_RECON="$RECON"

python3 - "$SESSION" "$PLAN" <<'PY'
import json, os, pathlib, re, shutil, subprocess, sys

session_dir = pathlib.Path(sys.argv[1])
plan_path = pathlib.Path(sys.argv[2])
evid = session_dir / "evidence"
sources_dir = evid / "sources"

SECRET_LINE = re.compile(r"(?i)^\s*(export\s+)?[A-Z0-9_]*(PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)[A-Z0-9_]*\s*=")

def scrub(text):
    out, notes = [], []
    for line in text.splitlines(True):
        if SECRET_LINE.search(line):
            out.append("# redacted secret-looking assignment\n")
            notes.append("redacted_secret_line")
            continue
        out.append(line)
    return "".join(out), notes

def split_env(name):
    raw = os.environ.get(name) or ""
    return [x for x in raw.split("\036") if x]

notes = []
plan_text, n = scrub(plan_path.read_text(encoding="utf-8", errors="replace"))
notes.extend(n)
(evid / "plan.md").write_text(plan_text, encoding="utf-8")

source_docs = []
for src in split_env("YONKO_PLAN_SOURCES"):
    p = pathlib.Path(src)
    if not p.exists():
        raise SystemExit(f"yonko: source not found: {src}")
    text, n = scrub(p.read_text(encoding="utf-8", errors="replace"))
    notes.extend(n)
    dest = sources_dir / p.name
    dest.write_text(text, encoding="utf-8")
    source_docs.append({"name": p.name, "origin": str(p)})

recon_path = os.environ.get("YONKO_PLAN_RECON") or ""
recon_written = False
if recon_path:
    rp = pathlib.Path(recon_path)
    if not rp.exists():
        raise SystemExit(f"yonko: recon file not found: {recon_path}")
    text, n = scrub(rp.read_text(encoding="utf-8", errors="replace"))
    notes.extend(n)
    (evid / "recon.md").write_text(text, encoding="utf-8")
    recon_written = True

def label_for(path):
    p = pathlib.Path(path).resolve()
    parts = p.parts
    for i, part in enumerate(parts):
        if part in ("services", "frontend", "models", "platform", "clients") and i + 1 < len(parts):
            return f"{part}/{parts[i+1]}"
    return p.name

def run(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

repo_docs = []
ctx_lines = []
for repo in split_env("YONKO_PLAN_REPOS"):
    rp = pathlib.Path(repo).resolve()
    if not rp.exists():
        raise SystemExit(f"yonko: repo not found: {repo}")
    label = label_for(rp)
    branch = "unknown"
    head = ""
    dirty = False
    if (rp / ".git").exists():
        _, b, _ = run(rp, "git", "branch", "--show-current")
        branch = b.strip() or "DETACHED"
        _, h, _ = run(rp, "git", "log", "-1", "--oneline")
        head = h.strip()
        _, st, _ = run(rp, "git", "status", "--short")
        dirty = bool(st.strip())
    repo_docs.append({"label": label, "path": str(rp), "branch": branch, "head": head, "dirty": dirty})
    ctx_lines.append(f"=== REPO: {label} ===")
    ctx_lines.append(f" path: {rp}")
    ctx_lines.append(f" branch: {branch}")
    ctx_lines.append(f" head: {head or 'n/a'}")
    ctx_lines.append(f" dirty: {dirty}")
    ctx_lines.append("")

(evid / "REPO_CONTEXT.txt").write_text("\n".join(ctx_lines) + "\n" if ctx_lines else "(no repositories named)\n", encoding="utf-8")

refs = {
    "review_type": "plan",
    "plan": {"origin": str(plan_path), "bytes": len(plan_text.encode("utf-8"))},
    "sources": source_docs,
    "recon": recon_written,
    "repositories_named": repo_docs,
    "scrub_notes": sorted(set(notes)),
}
(evid / "plan-refs.json").write_text(json.dumps(refs, indent=2) + "\n", encoding="utf-8")

print(json.dumps({
    "ok": True,
    "sources": len(source_docs),
    "repositories_named": len(repo_docs),
    "recon": recon_written,
}, indent=2))
PY

"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type plan_evidence_collected --data "{\"repos_named\":${#REPOS[@]},\"sources\":${#SOURCES[@]}}"
yonko_info "plan evidence written to $EVID"
