#!/usr/bin/env bash
# Optional: install Yonko-friendly Cursor Auto-review hints into ~/.cursor/
# Recommended path is Run Everything plus Destructive Command Guard (see SHARE.md).
set -euo pipefail

YONKO_SKILL="$(cd "$(dirname "$0")/.." && pwd)"
EX="$YONKO_SKILL/examples/cursor-autorun"
DEST="${HOME}/.cursor"
HOME_ABS="${HOME}"

ok() { echo "  ok  $1"; }
warn() { echo "  !!  $1"; }
die() { echo "yonko install-cursor-autorun: $*" >&2; exit 1; }

[[ -d "$EX" ]] || die "missing $EX"
mkdir -p "$DEST"

python3 - "$EX" "$DEST" "$HOME_ABS" <<'PY'
import json, sys
from pathlib import Path

ex, dest, home = map(Path, sys.argv[1:4])

def load(p: Path):
    return json.loads(p.read_text()) if p.is_file() else {}

def uniq(seq):
    out, seen = [], set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

# permissions: merge autoRun instruction arrays
src_perm = load(ex / "permissions.json")
dst_perm_path = dest / "permissions.json"
dst_perm = load(dst_perm_path)
src_ar = src_perm.get("autoRun") or {}
dst_ar = dst_perm.get("autoRun") or {}
for key in ("allow_instructions", "block_instructions"):
    dst_ar[key] = uniq(list(dst_ar.get(key) or []) + list(src_ar.get(key) or []))
dst_perm["autoRun"] = dst_ar
# Do not invent terminalAllowlist here - that would wipe IDE allowlists.
dst_perm_path.write_text(json.dumps(dst_perm, indent=2) + "\n")
print(f"wrote {dst_perm_path}")

# sandbox: substitute home + union paths
raw = (ex / "sandbox.json").read_text().replace("HOME_PLACEHOLDER", str(home))
src_sb = json.loads(raw)
dst_sb_path = dest / "sandbox.json"
dst_sb = load(dst_sb_path)
if not dst_sb:
    dst_sb = {"type": "workspace_readwrite"}
dst_sb.setdefault("type", src_sb.get("type", "workspace_readwrite"))
for key in ("additionalReadonlyPaths", "additionalReadwritePaths"):
    dst_sb[key] = uniq(list(dst_sb.get(key) or []) + list(src_sb.get(key) or []))
if src_sb.get("enableSharedBuildCache"):
    dst_sb["enableSharedBuildCache"] = True
dst_sb_path.write_text(json.dumps(dst_sb, indent=2) + "\n")
print(f"wrote {dst_sb_path}")
PY

ok "Cursor Auto-review files installed under $DEST"
echo
echo "In Cursor (this optional Auto-review path):"
echo "  Settings → Agents → Approvals & Execution → Run Mode → Auto-review"
echo "  Then start a new agent chat (or restart Cursor)."
echo
echo "Recommended instead: dcg + Run Everything (SHARE.md)."
