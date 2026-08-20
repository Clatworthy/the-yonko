#!/usr/bin/env bash
# build-evidence-graph.sh - deterministic Evidence Graph for a Yonko session.
# Usage:
#   build-evidence-graph.sh --session DIR [--skip-completeness]
#   build-evidence-graph.sh --session DIR --waive --waive-reason TEXT --approved-by WHO
#
# Writes:
#   evidence/evidence-graph.json
#   evidence/graph-completeness.json
#   evidence/evidence-graph-report.md
#
# Does not change risk classification, seating, or model policy.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
SKIP_COMPLETENESS=0
WAIVE=0
WAIVE_REASON=""
APPROVED_BY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --skip-completeness) SKIP_COMPLETENESS=1; shift ;;
    --waive) WAIVE=1; shift ;;
    --waive-reason) WAIVE_REASON="${2:-}"; shift 2 ;;
    --approved-by) APPROVED_BY="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: build-evidence-graph.sh --session DIR [--waive --waive-reason TEXT --approved-by WHO]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
export YONKO_SCRIPTS_DIR="$SCRIPT_DIR"

python3 - "$SESSION" "$SKIP_COMPLETENESS" "$WAIVE" "$WAIVE_REASON" "$APPROVED_BY" <<'PY'
import json, pathlib, sys, os, importlib.util

session_dir = pathlib.Path(sys.argv[1])
skip = sys.argv[2] == "1"
waive = sys.argv[3] == "1"
waive_reason = sys.argv[4]
approved_by = sys.argv[5]

scripts = pathlib.Path(os.environ.get("YONKO_SCRIPTS_DIR") or str(pathlib.Path(__file__).resolve().parent))
lib = scripts / "lib" / "evidence_graph" / "build.py"
spec = importlib.util.spec_from_file_location("yonko_evidence_graph_build", lib)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

evid = session_dir / "evidence"
evid.mkdir(parents=True, exist_ok=True)

graph = mod.build_evidence_graph(session_dir)
(evid / "evidence-graph.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

completeness = None
if not skip:
    completeness = mod.evaluate_completeness(
        session_dir, graph=graph, waive=waive, waive_reason=waive_reason, approved_by=approved_by
    )
    (evid / "graph-completeness.json").write_text(
        json.dumps(completeness, indent=2) + "\n", encoding="utf-8"
    )
    report = mod.render_report(graph, completeness)
    (evid / "evidence-graph-report.md").write_text(report, encoding="utf-8")

print(json.dumps({
    "ok": True,
    "nodes": graph["metrics"]["nodes"],
    "edges": graph["metrics"]["edges"],
    "changed_symbols": graph["metrics"]["changed_symbols"],
    "unresolved_edges": graph["metrics"]["unresolved_edges"],
    "ok_for_seating": None if completeness is None else completeness["ok_for_seating"],
    "blocks_seating": None if completeness is None else completeness["blocks_seating"],
    "blocks_complete_verdict": None if completeness is None else completeness["blocks_complete_verdict"],
}, indent=2))
if completeness is not None and not completeness["ok_for_seating"]:
    sys.exit(3)
PY

# record events (best-effort)
"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type evidence_graph_built \
  --data "$(python3 -c 'import json,pathlib,sys; g=json.loads(pathlib.Path(sys.argv[1]).read_text()); print(json.dumps({"nodes":g["metrics"]["nodes"],"edges":g["metrics"]["edges"],"duration_ms":g["metrics"]["duration_ms"]}))' "$SESSION/evidence/evidence-graph.json")" || true

if [[ "$SKIP_COMPLETENESS" -eq 0 && -f "$SESSION/evidence/graph-completeness.json" ]]; then
  "$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type evidence_completeness_evaluated \
    --data "$(python3 -c 'import json,pathlib,sys; c=json.loads(pathlib.Path(sys.argv[1]).read_text()); print(json.dumps({"ok_for_seating":c["ok_for_seating"],"blocks_seating":c["blocks_seating"],"blocks_complete_verdict":c["blocks_complete_verdict"]}))' "$SESSION/evidence/graph-completeness.json")" || true
fi
