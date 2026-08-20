#!/usr/bin/env bash
# collect-document-evidence.sh - evidence collector for DOCUMENT review (V3).
# Supports create mode (no draft yet) and review mode (existing draft).
#
# Usage:
#   collect-document-evidence.sh --session DIR --artifact pap|prd|adr|design
#     [--draft FILE]                # required for --mode review
#     [--mode create|review]        # default: review when --draft given, else create
#     [--source FILE]...            # ticket, notes, problem statement, prior docs
#     [--recon FILE]                # Chair reconnaissance notes
#     [--repo ABS_PATH]...          # repositories inspected for claim validation
#
# Writes: evidence/document.md (review mode), evidence/sources/, evidence/recon.md,
#         evidence/doc-refs.json, evidence/REPO_CONTEXT.txt, evidence/SECTION_MAP.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
ARTIFACT=""
DRAFT=""
MODE=""
RECON=""
SOURCES=()
REPOS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --artifact) ARTIFACT="${2:-}"; shift 2 ;;
    --draft) DRAFT="${2:-}"; shift 2 ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --source) SOURCES+=("${2:-}"); shift 2 ;;
    --recon) RECON="${2:-}"; shift 2 ;;
    --repo) REPOS+=("${2:-}"); shift 2 ;;
    -h|--help)
      echo "Usage: collect-document-evidence.sh --session DIR --artifact pap|prd|adr|design [--draft FILE] [--mode create|review] [--source FILE]... [--recon FILE] [--repo ABS]..."
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
case "$ARTIFACT" in
  pap|prd|adr|design) ;;
  *) yonko_die "--artifact must be pap|prd|adr|design" ;;
esac

if [[ -z "$MODE" ]]; then
  if [[ -n "$DRAFT" ]]; then MODE="review"; else MODE="create"; fi
fi
case "$MODE" in
  create|review) ;;
  *) yonko_die "--mode must be create|review" ;;
esac
if [[ "$MODE" == "review" ]]; then
  [[ -n "$DRAFT" && -f "$DRAFT" ]] || yonko_die "review mode requires --draft FILE"
fi
if [[ "$MODE" == "create" && ${#SOURCES[@]} -eq 0 ]]; then
  yonko_die "create mode requires at least one --source (ticket, notes or problem statement)"
fi

EVID="$SESSION/evidence"
mkdir -p "$EVID/sources"

export YONKO_DOC_SOURCES="$(
  if [[ ${#SOURCES[@]} -gt 0 ]]; then printf '%s\036' "${SOURCES[@]}"; fi
)"
export YONKO_DOC_REPOS="$(
  if [[ ${#REPOS[@]} -gt 0 ]]; then printf '%s\036' "${REPOS[@]}"; fi
)"
export YONKO_DOC_RECON="$RECON"
export YONKO_DOC_ARTIFACT="$ARTIFACT"
export YONKO_DOC_MODE="$MODE"
export YONKO_DOC_DRAFT="$DRAFT"

python3 - "$SESSION" <<'PY'
import json, os, pathlib, re, subprocess, sys

session_dir = pathlib.Path(sys.argv[1])
evid = session_dir / "evidence"
sources_dir = evid / "sources"
artifact = os.environ["YONKO_DOC_ARTIFACT"]
mode = os.environ["YONKO_DOC_MODE"]
draft = os.environ.get("YONKO_DOC_DRAFT") or ""

SECRET_LINE = re.compile(r"(?i)^\s*(export\s+)?[A-Z0-9_]*(PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)[A-Z0-9_]*\s*=")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

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
doc_meta = None
section_lines = []

if mode == "review":
    dp = pathlib.Path(draft)
    text, n = scrub(dp.read_text(encoding="utf-8", errors="replace"))
    notes.extend(n)
    (evid / "document.md").write_text(text, encoding="utf-8")
    doc_meta = {"origin": str(dp), "bytes": len(text.encode("utf-8"))}
    for i, line in enumerate(text.splitlines(), start=1):
        m = HEADING.match(line)
        if m:
            section_lines.append(f"L{i}\t{m.group(1)}\t{m.group(2)}")

(evid / "SECTION_MAP.txt").write_text(
    ("\n".join(section_lines) + "\n") if section_lines else "(no draft; create mode)\n",
    encoding="utf-8",
)

source_docs = []
for src in split_env("YONKO_DOC_SOURCES"):
    p = pathlib.Path(src)
    if not p.exists():
        raise SystemExit(f"yonko: source not found: {src}")
    text, n = scrub(p.read_text(encoding="utf-8", errors="replace"))
    notes.extend(n)
    (sources_dir / p.name).write_text(text, encoding="utf-8")
    source_docs.append({"name": p.name, "origin": str(p)})

recon_path = os.environ.get("YONKO_DOC_RECON") or ""
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
for repo in split_env("YONKO_DOC_REPOS"):
    rp = pathlib.Path(repo).resolve()
    if not rp.exists():
        raise SystemExit(f"yonko: repo not found: {repo}")
    label = label_for(rp)
    branch, head = "unknown", ""
    if (rp / ".git").exists():
        _, b, _ = run(rp, "git", "branch", "--show-current")
        branch = b.strip() or "DETACHED"
        _, h, _ = run(rp, "git", "log", "-1", "--oneline")
        head = h.strip()
    repo_docs.append({"label": label, "path": str(rp), "branch": branch, "head": head})
    ctx_lines.append(f"=== REPO: {label} ===")
    ctx_lines.append(f" path: {rp}")
    ctx_lines.append(f" branch: {branch}")
    ctx_lines.append(f" head: {head or 'n/a'}")
    ctx_lines.append("")

(evid / "REPO_CONTEXT.txt").write_text(
    ("\n".join(ctx_lines) + "\n") if ctx_lines else "(no repositories inspected)\n", encoding="utf-8"
)

refs = {
    "review_type": "document",
    "artifact_type": artifact,
    "mode": mode,
    "document": doc_meta,
    "sections": len(section_lines),
    "sources": source_docs,
    "recon": recon_written,
    "repositories_inspected": repo_docs,
    "scrub_notes": sorted(set(notes)),
}
(evid / "doc-refs.json").write_text(json.dumps(refs, indent=2) + "\n", encoding="utf-8")

print(json.dumps({
    "ok": True,
    "artifact": artifact,
    "mode": mode,
    "sections": len(section_lines),
    "sources": len(source_docs),
    "repositories_inspected": len(repo_docs),
    "recon": recon_written,
}, indent=2))
PY

"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type document_evidence_collected \
  --data "{\"artifact\":\"$ARTIFACT\",\"mode\":\"$MODE\",\"repos\":${#REPOS[@]},\"sources\":${#SOURCES[@]}}"
yonko_info "document evidence written to $EVID"
