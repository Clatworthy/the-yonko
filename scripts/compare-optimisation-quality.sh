#!/usr/bin/env bash
# compare-optimisation-quality.sh - record dual-packet quality acceptance.
#
# Mechanical compare is automatic. Council compare is Chair-filled JSON.
#
# Usage:
#   compare-optimisation-quality.sh \
#     --original <packet-or-session> \
#     --optimised <packet-or-session> \
#     --out <dir> \
#     [--council-json <filled-compare.json>]
#
# Exit 0 only if mechanical preservation passes AND (if council JSON given)
# the council compare evaluates to pass.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

ORIGINAL=""
OPTIMISED=""
OUT=""
COUNCIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --original) ORIGINAL="${2:-}"; shift 2 ;;
    --optimised) OPTIMISED="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --council-json) COUNCIL="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: compare-optimisation-quality.sh --original PATH --optimised PATH --out DIR [--council-json FILE]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

[[ -n "$ORIGINAL" && -n "$OPTIMISED" && -n "$OUT" ]] || yonko_die "--original, --optimised, --out required"

resolve_packet() {
  local p="$1"
  if [[ -f "$p" && "$p" == *.md ]]; then
    echo "$p"
    return
  fi
  if [[ -d "$p" && -f "$p/packet.md" ]]; then
    echo "$p/packet.md"
    return
  fi
  yonko_die "need packet.md file or session dir with packet.md: $p"
}

ORIG_PKT="$(resolve_packet "$ORIGINAL")"
OPT_PKT="$(resolve_packet "$OPTIMISED")"
mkdir -p "$OUT"
export YONKO_SCRIPTS_DIR="$SCRIPT_DIR"

python3 - "$ORIG_PKT" "$OPT_PKT" "$OUT" "${COUNCIL:-}" <<'PY'
import json, pathlib, sys, importlib.util

orig, opt, out, council_path = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]), sys.argv[4]
lib = pathlib.Path(__file__).resolve().parent / "lib" / "information_preservation.py" if False else None
import os
scripts = pathlib.Path(os.environ.get("YONKO_SCRIPTS_DIR") or str(pathlib.Path.home() / ".cursor/skills/the-yonko/scripts"))
spec = importlib.util.spec_from_file_location("yonko_information_preservation", scripts / "lib" / "information_preservation.py")
ip = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ip
assert spec.loader
spec.loader.exec_module(ip)

o = pathlib.Path(orig).read_text(encoding="utf-8")
n = pathlib.Path(opt).read_text(encoding="utf-8")
mech = ip.compare_packets(o, n)
council = None
if council_path:
    raw = json.loads(pathlib.Path(council_path).read_text(encoding="utf-8"))
    # map pass -> pass_
    if "pass" in raw and "pass_" not in raw:
        raw["pass_"] = raw.pop("pass")
    council = ip.CouncilCompareRecord(**{k: v for k, v in raw.items() if k in ip.CouncilCompareRecord.__dataclass_fields__})
    council.evaluate()

ip.write_preservation_report(out / "preservation-report.json", mech, council)
(out / "ORIGINAL.packet.md").write_text(o, encoding="utf-8")
(out / "OPTIMISED.packet.md").write_text(n, encoding="utf-8")

summary = {
    "mechanical_ok": mech.ok,
    "bytes_saved": mech.bytes_saved,
    "estimated_token_saved": mech.estimated_token_saved,
    "council_pass": None if council is None else council.pass_,
    "errors": mech.errors + ([] if council is None else council.unexplained_differences),
}
(out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
if not mech.ok:
    raise SystemExit(1)
if council is not None and not council.pass_:
    raise SystemExit(1)
PY

yonko_info "preservation report: $OUT/preservation-report.json"
