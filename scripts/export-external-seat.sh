#!/usr/bin/env bash
# export-external-seat.sh - write a slim brief for Claude Code / Codex
#
# Usage:
#   export-external-seat.sh --session DIR --seat blackbeard|shanks
#
# Writes under SESSION/external/<seat>/:
#   SEAT.md            - short prompt (paths only - do not paste into CLI arg by hand)
#   packet.slim.md     - Docket + DIFF LABELS + DIFF MAP + DIFF hunks only
#   packet.slim.meta.json - sha256 of slim packet (what the external seat verifies)
#   packet.meta.json   - copy of session pin (session packet_hash for Chair correlation)
#   README.md
#
# Does NOT invoke CLIs. Does NOT run smoke tests (those stay on Cursor Models).
# See run-external-seat.sh for e2e.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
SEAT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --seat) SEAT="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: export-external-seat.sh --session DIR --seat blackbeard|shanks"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
SESSION="$(cd "$SESSION" && pwd)"
[[ -n "$SEAT" ]] || yonko_die "--seat required (blackbeard|shanks)"
SEAT="$(echo "$SEAT" | tr '[:upper:]' '[:lower:]')"

case "$SEAT" in
  blackbeard)
    SEAT_NAME="Blackbeard"
    SEAT_KEY="blackbeard"
    SEAT_LENS="correctness, concurrency, retries, golden-path parity, TOCTOU, side-effect leaves"
    ID_PREFIX="Bb"
    ;;
  shanks)
    SEAT_NAME="Shanks"
    SEAT_KEY="shanks"
    SEAT_LENS="contracts, compatibility, requirements, API shapes, auth boundaries"
    ID_PREFIX="S"
    ;;
  *)
    yonko_die "seat must be blackbeard or shanks (external Other Models fallback)"
    ;;
esac

PACKET="$SESSION/packet.md"
META="$SESSION/packet.meta.json"
[[ -f "$PACKET" ]] || yonko_die "missing packet.md - pin packet first"
[[ -f "$META" ]] || yonko_die "missing packet.meta.json"

SESSION_PACKET_HASH="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["packet_hash"])' "$META")"
OUT_DIR="$SESSION/external/$SEAT_KEY"
mkdir -p "$OUT_DIR"
cp "$META" "$OUT_DIR/packet.meta.json"

# Slim packet: keep review substance; drop routing/ceremony that burns CLI tokens.
python3 - "$PACKET" "$OUT_DIR/packet.slim.md" "$OUT_DIR/packet.slim.meta.json" "$SESSION_PACKET_HASH" <<'PY'
import hashlib, json, pathlib, sys

src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
slim_path = pathlib.Path(sys.argv[2])
meta_path = pathlib.Path(sys.argv[3])
session_hash = sys.argv[4]

keep_prefixes = (
    "=== YONKO DOCKET ===",
    "=== DIFF LABELS",
    "=== DIFF MAP ===",
    "=== DIFF:",
)
# Also keep APPROVED PLAN handoff when present (plan-linked implementation).
keep_prefixes = keep_prefixes + (
    "=== APPROVED PLAN",
    "=== EVIDENCE GRAPH",
    "=== EVIDENCE COMPLETENESS",
    "=== EVIDENCE GRAPH REPORT",
)

sections: list[str] = []
current: list[str] = []
keeping = False

def flush():
    global current, keeping
    if keeping and current:
        sections.append("".join(current).rstrip() + "\n\n")
    current = []
    keeping = False

for line in src.splitlines(True):
    if line.startswith("==="):
        flush()
        keeping = any(line.startswith(p) for p in keep_prefixes)
        if keeping:
            current.append(line)
        continue
    if keeping:
        current.append(line)
flush()

header = (
    "=== YONKO EXTERNAL SLIM PACKET ===\n"
    f"Session packet_hash (Chair pin): {session_hash}\n"
    "This file omits REPOS paths, CHANGE CLASSES, and REVIEWER ROUTING to save tokens.\n"
    "Verify slim_packet_hash below after reading.\n\n"
)
body = "".join(sections)
text = header + body
slim_path.write_text(text, encoding="utf-8")
slim_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
meta_path.write_text(
    json.dumps(
        {
            "slim_packet_hash": slim_hash,
            "session_packet_hash": session_hash,
            "bytes": len(text.encode("utf-8")),
            "kept_sections": [
                "YONKO DOCKET",
                "APPROVED PLAN (if present)",
                "DIFF LABELS",
                "DIFF MAP",
                "DIFF:*",
                "EVIDENCE GRAPH (if present)",
                "EVIDENCE COMPLETENESS (if present)",
                "EVIDENCE GRAPH REPORT (if present)",
            ],
            "dropped_sections": ["REPOS", "CHANGE CLASSES", "REVIEWER ROUTING", "other ceremony"],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print(json.dumps({"slim_packet_hash": slim_hash, "bytes": len(text.encode("utf-8"))}), file=__import__("sys").stderr)
PY

SLIM_HASH="$(python3 -c 'import json; print(json.load(open("'"$OUT_DIR"'/packet.slim.meta.json"))["slim_packet_hash"])')"
SLIM_BYTES="$(python3 -c 'import json; print(json.load(open("'"$OUT_DIR"'/packet.slim.meta.json"))["bytes"])')"
FULL_BYTES="$(wc -c < "$PACKET" | tr -d ' ')"

PACKET_PATH="$OUT_DIR/packet.slim.md"
OUTPUT_PATH="$OUT_DIR/findings.json"
TEMPLATE="$SCRIPT_DIR/../templates/external-seat-prompt.md"
[[ -f "$TEMPLATE" ]] || yonko_die "missing template: $TEMPLATE"

python3 - "$TEMPLATE" "$OUT_DIR/SEAT.md" <<PY
import pathlib, sys
tpl = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
out = pathlib.Path(sys.argv[2])
repl = {
    "{{SEAT_NAME}}": "$SEAT_NAME",
    "{{SEAT_KEY}}": "$SEAT_KEY",
    "{{SEAT_LENS}}": "$SEAT_LENS",
    "{{PACKET_HASH}}": "$SLIM_HASH",
    "{{PACKET_PATH}}": "$PACKET_PATH",
    "{{ID_PREFIX}}": "$ID_PREFIX",
    "{{OUTPUT_PATH}}": "$OUTPUT_PATH",
}
text = tpl
for k, v in repl.items():
    text = text.replace(k, v)
# Clarify session pin correlation
text = text.replace(
    "Packet hash (must match file):",
    "Slim packet hash (must match packet.slim.md bytes):",
)
text += (
    "\n\n## Session correlation (do not require re-reading the full Cursor packet)\n"
    f"- Session packet_hash (Chair pin): \`$SESSION_PACKET_HASH\`\n"
    f"- Slim bytes: $SLIM_BYTES (full packet was ~$FULL_BYTES bytes)\n"
)
out.write_text(text, encoding="utf-8")
PY

cat > "$OUT_DIR/README.md" <<EOF
# External Yonko seat: $SEAT_NAME

- Session packet_hash: \`$SESSION_PACKET_HASH\`
- Slim packet hash: \`$SLIM_HASH\` ($SLIM_BYTES bytes; full was ~$FULL_BYTES)

Smoke / health checks stay on **Cursor Models** (Grok / Composer). Do not burn Claude Code or Codex on "ok" probes.

## Automated

\`\`\`bash
scripts/run-external-seat.sh --session "$SESSION" --seat $SEAT_KEY
\`\`\`

CLI is given **paths only** (no inlined packet). It must read:
- \`$OUT_DIR/SEAT.md\`
- \`$PACKET_PATH\`

## Manual

Point Claude Code / Codex at \`SEAT.md\` only. Save JSON to \`$OUTPUT_PATH\`.
EOF

python3 - <<PY
import json
from pathlib import Path
meta = {
    "seat": "$SEAT_KEY",
    "seat_name": "$SEAT_NAME",
    "session_packet_hash": "$SESSION_PACKET_HASH",
    "slim_packet_hash": "$SLIM_HASH",
    "slim_bytes": int("$SLIM_BYTES"),
    "full_bytes": int("$FULL_BYTES"),
    "brief": str(Path("$OUT_DIR/SEAT.md")),
    "packet": str(Path("$PACKET_PATH")),
    "output": str(Path("$OUTPUT_PATH")),
    "channel": "external_cli_path_only",
    "no_smoke_via_cli": True,
}
Path("$OUT_DIR/export.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"ok": True, "type": "external_seat_exported", **meta}, indent=2))
PY
