#!/usr/bin/env bash
# collect-evidence.sh - gather git evidence for one or more repo roots into a session.
# Usage:
#   collect-evidence.sh --session <dir> --repo <abs-path> [--repo <abs-path> ...]
#   collect-evidence.sh --session <dir> --workspace <repo-root>   # auto-discover dirty repos
#
# Writes:
#   evidence/repos.json
#   evidence/DIFF-<label>.patch
#   evidence/DIFF_MAP.txt
#   evidence/STATUS.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
WORKSPACE=""
REPOS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --repo) REPOS+=("${2:-}"); shift 2 ;;
    -h|--help)
      echo "Usage: collect-evidence.sh --session DIR (--repo ABS | --workspace ROOT)..."
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
EVID="$SESSION/evidence"
mkdir -p "$EVID"

# Auto-discover dirty git repos under a multi-repo workspace root
if [[ -n "$WORKSPACE" ]]; then
  [[ -d "$WORKSPACE" ]] || yonko_die "workspace not found: $WORKSPACE"
  while IFS= read -r -d '' gitdir; do
    root="$(dirname "$gitdir")"
    # skip huge / irrelevant trees
    case "$root" in
      */node_modules/*|*/.gradle/*|*/build/*|*/.git) continue ;;
    esac
    if git -C "$root" status --porcelain 2>/dev/null | grep -q .; then
      REPOS+=("$root")
    fi
  done < <(find "$WORKSPACE" -maxdepth 4 -type d -name .git -print0 2>/dev/null)
fi

[[ ${#REPOS[@]} -gt 0 ]] || yonko_die "no repos provided and none dirty under workspace"

python3 - "$SESSION" "$EVID" "${REPOS[@]}" <<'PY'
import json, os, subprocess, pathlib, sys, re

session_dir = pathlib.Path(sys.argv[1])
evid = pathlib.Path(sys.argv[2])
repos = sys.argv[3:]

SECRET_NAME_RE = re.compile(r"(?i)((?:^|/)\.env(?:\.[^/]+)?$|\.env\.local|credentials|id_rsa|\.pem$)")
SECRET_LINE_RE = re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*=")

def run(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def label_for(path, workspace_hint=None):
    p = pathlib.Path(path).resolve()
    parts = p.parts
    for i, part in enumerate(parts):
        if part in ("services", "frontend", "models", "platform", "clients") and i + 1 < len(parts):
            return f"{part}/{parts[i+1]}"
    return p.name

repo_docs = []
map_lines = ["=== DIFF MAP ==="]
status_lines = []

for repo in repos:
    repo_path = pathlib.Path(repo).resolve()
    if not (repo_path / ".git").exists():
        raise SystemExit(f"yonko: not a git repo: {repo_path}")
    label = label_for(repo_path)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label)
    code, branch, _ = run(repo_path, "git", "branch", "--show-current")
    branch = branch.strip() or "DETACHED"
    _, status, _ = run(repo_path, "git", "status", "--short")
    _, diff, _ = run(repo_path, "git", "diff")
    _, cached, _ = run(repo_path, "git", "diff", "--cached")
    _, log, _ = run(repo_path, "git", "log", "-8", "--oneline")

    combined = ""
    if cached.strip():
        combined += cached
        if not combined.endswith("\n"):
            combined += "\n"
    if diff.strip():
        combined += diff
        if not combined.endswith("\n"):
            combined += "\n"

    # Include untracked files as synthetic new-file diffs so seats see edge/CI
    # content that is not yet `git add`ed (packet gap that missed CF querystring).
    _, untracked, _ = run(repo_path, "git", "ls-files", "--others", "--exclude-standard")
    max_untracked_bytes = 200_000
    for rel in untracked.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        if SECRET_NAME_RE.search(rel):
            skip_paths.add(rel)
            combined += f"# secrets excluded (untracked): {rel}\n"
            continue
        abs_path = repo_path / rel
        if not abs_path.is_file():
            continue
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size > max_untracked_bytes:
            combined += f"# untracked skipped (too large {size} bytes): {rel}\n"
            continue
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            combined += f"# untracked skipped (binary or unreadable): {rel}\n"
            continue
        combined += f"diff --git a/{rel} b/{rel}\n"
        combined += "new file mode 100644\n"
        combined += f"--- /dev/null\n+++ b/{rel}\n"
        for line in text.splitlines():
            combined += f"+{line}\n"
        if not text.endswith("\n"):
            combined += "\\ No newline at end of file\n"

    # Secret fence: drop files whose path looks secret-bearing
    filtered = []
    skip_paths = set()
    current_file = None
    for line in combined.splitlines(True):
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)", line)
            current_file = m.group(2) if m else None
            if current_file and SECRET_NAME_RE.search(current_file):
                skip_paths.add(current_file)
                filtered.append(f"# secrets excluded: {current_file}\n")
                current_file = f"__skip__:{current_file}"
                continue
        if current_file and current_file.startswith("__skip__:"):
            continue
        if SECRET_LINE_RE.search(line) and not line.startswith("+++") and not line.startswith("---"):
            filtered.append("# redacted secret-looking line\n")
            continue
        filtered.append(line)
    combined = "".join(filtered)

    patch_path = evid / f"DIFF-{safe}.patch"
    patch_path.write_text(combined, encoding="utf-8")

    # file summary from status
    files = []
    for line in status.splitlines():
        files.append(line.strip())
    map_lines.append(f"repo: {label}")
    map_lines.append(f" branch: {branch}")
    map_lines.append(f" files: {', '.join(files) if files else '(clean?)'}")
    map_lines.append(f" summary: collected by collect-evidence.sh")
    map_lines.append(f" patch: evidence/DIFF-{safe}.patch")

    status_lines.append(f"=== {label} ===")
    status_lines.append(status or "(clean)")
    status_lines.append("")
    status_lines.append(log)
    status_lines.append("")

    repo_docs.append({
        "label": label,
        "path": str(repo_path),
        "branch": branch,
        "patch": f"DIFF-{safe}.patch",
        "secrets_excluded": sorted(skip_paths),
        "dirty": bool(status.strip()),
    })

(evid / "DIFF_MAP.txt").write_text("\n".join(map_lines) + "\n", encoding="utf-8")
(evid / "STATUS.txt").write_text("\n".join(status_lines) + "\n", encoding="utf-8")
(evid / "repos.json").write_text(json.dumps({"repos": repo_docs}, indent=2) + "\n", encoding="utf-8")

labels = [r["label"] for r in repo_docs]
print(json.dumps({"ok": True, "repos": labels, "count": len(labels)}))
PY

"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type evidence_collected --data "{\"repo_count\":${#REPOS[@]}}"
yonko_info "evidence written to $EVID"
