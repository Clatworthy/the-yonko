#!/usr/bin/env bash
# Generic Yonko install helper (anyone - no org-specific wiring).
# Creates config/project-adapters.local.yaml from the example if missing.
# Does not enable Luffy until you point skill paths at your company's requirements.
#
# Usage:
#   bash ~/.cursor/skills/the-yonko/scripts/setup.sh
#   bash ~/.cursor/skills/the-yonko/scripts/setup.sh --cursor-autorun
#   YONKO_PROJECT_ROOT=/path/to/checkout bash ~/.cursor/skills/the-yonko/scripts/setup.sh
#
set -euo pipefail

YONKO_SKILL="$(cd "$(dirname "$0")/.." && pwd)"
EXAMPLE="$YONKO_SKILL/examples/org-standards/project-adapters.yaml"
LOCAL="$YONKO_SKILL/config/project-adapters.local.yaml"
ROOT="${YONKO_PROJECT_ROOT:-}"
INSTALL_CURSOR_AUTORUN=0
for arg in "$@"; do
  case "$arg" in
    --cursor-autorun) INSTALL_CURSOR_AUTORUN=1 ;;
    -h|--help)
      echo "usage: setup.sh [--cursor-autorun]"
      exit 0
      ;;
  esac
done

ok() { echo "  ok  $1"; }
warn() { echo "  !!  $1"; }
die() { echo "yonko setup: $*" >&2; exit 1; }

echo "Yonko setup (generic)"
echo "  skill: $YONKO_SKILL"
[[ -f "$YONKO_SKILL/SKILL.md" ]] || die "SKILL.md missing"
ok "skill present"

mkdir -p "$YONKO_SKILL/config"
if [[ -f "$LOCAL" ]]; then
  ok "adapter already exists: $LOCAL (left untouched)"
else
  [[ -f "$EXAMPLE" ]] || die "missing example: $EXAMPLE"
  cp "$EXAMPLE" "$LOCAL"
  ok "wrote $LOCAL from examples/org-standards/"
  warn "edit that file: set path_contains, workspace_markers, and luffy.skills"
  warn "Luffy stays abroad until those paths point at your company's requirements"
fi

if [[ -n "$ROOT" ]]; then
  ok "YONKO_PROJECT_ROOT=$ROOT"
else
  warn "set YONKO_PROJECT_ROOT to your checkout root when seating Luffy"
fi

if [[ "$INSTALL_CURSOR_AUTORUN" == "1" ]]; then
  echo
  bash "$YONKO_SKILL/scripts/install-cursor-autorun.sh"
fi

echo
echo "Next:"
echo "  1. Edit $LOCAL (company-specific requirement paths, if you want Luffy)"
echo "  2. Open your project workspace in Cursor"
echo "  3. Install dcg: https://github.com/Dicklesworthstone/destructive_command_guard"
echo "  4. Settings → Agents → Approvals & Execution → Run Everything"
echo "  5. Optional Auto-review instead: setup.sh --cursor-autorun"
echo "  6. Run: Consult the Yonko"
echo
echo "Council works without Luffy. Enable him only after the adapter points at your company's requirements."
echo "Author: Benjamin Clatworthy - https://github.com/Clatworthy/the-yonko"
