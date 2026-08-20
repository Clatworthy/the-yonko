#!/usr/bin/env bash
# evaluate-evidence-completeness.sh - re-evaluate graph completeness gates.
# Usage:
#   evaluate-evidence-completeness.sh --session DIR [--waive --waive-reason TEXT --approved-by WHO]
# Exit 3 when blocks_seating and not waived.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
WAIVE=0
WAIVE_REASON=""
APPROVED_BY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --waive) WAIVE=1; shift ;;
    --waive-reason) WAIVE_REASON="${2:-}"; shift 2 ;;
    --approved-by) APPROVED_BY="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: evaluate-evidence-completeness.sh --session DIR [--waive ...]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -f "$SESSION/evidence/evidence-graph.json" ]] || yonko_die "missing evidence/evidence-graph.json - run build-evidence-graph.sh first"
export YONKO_SCRIPTS_DIR="$SCRIPT_DIR"

python3 - "$SESSION" "$WAIVE" "$WAIVE_REASON" "$APPROVED_BY" <<'PY'
import json, pathlib, sys, os, importlib.util
session_dir = pathlib.Path(sys.argv[1])
waive = sys.argv[2] == "1"
lib = pathlib.Path(os.environ["YONKO_SCRIPTS_DIR"]) / "lib" / "evidence_graph" / "build.py"
spec = importlib.util.spec_from_file_location("yonko_eg", lib)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
c = mod.evaluate_completeness(session_dir, waive=waive, waive_reason=sys.argv[3], approved_by=sys.argv[4])
evid = session_dir / "evidence"
evid.joinpath("graph-completeness.json").write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8")
g = json.loads(evid.joinpath("evidence-graph.json").read_text(encoding="utf-8"))
evid.joinpath("evidence-graph-report.md").write_text(mod.render_report(g, c), encoding="utf-8")
print(json.dumps({"ok_for_seating": c["ok_for_seating"], "blocks_seating": c["blocks_seating"]}, indent=2))
sys.exit(0 if c["ok_for_seating"] else 3)
PY
