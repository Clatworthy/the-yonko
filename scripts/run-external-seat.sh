#!/usr/bin/env bash
# run-external-seat.sh - export + invoke Claude Code / Codex headless when available
#
# Usage:
#   run-external-seat.sh --session DIR --seat blackbeard|shanks [--export-only] [--force-cli claude|codex]
#
# Blackbeard → `claude -p` (company Claude login / API)
# Shanks     → `codex exec` (npx @openai/codex or codex on PATH)
#
# On success writes SESSION/external/<seat>/findings.json and records reviewers note.
# If CLI missing or fails: leaves SEAT.md for manual run (exit 2).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
SEAT=""
EXPORT_ONLY=0
FORCE_CLI=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --seat) SEAT="${2:-}"; shift 2 ;;
    --export-only) EXPORT_ONLY=1; shift ;;
    --force-cli) FORCE_CLI="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: run-external-seat.sh --session DIR --seat blackbeard|shanks [--export-only] [--force-cli claude|codex]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
SESSION="$(cd "$SESSION" && pwd)"
[[ -n "$SEAT" ]] || yonko_die "--seat required"

"$SCRIPT_DIR/export-external-seat.sh" --session "$SESSION" --seat "$SEAT"
SEAT="$(echo "$SEAT" | tr '[:upper:]' '[:lower:]')"
OUT_DIR="$SESSION/external/$SEAT"
BRIEF="$OUT_DIR/SEAT.md"
OUT_JSON="$OUT_DIR/findings.json"
RAW_OUT="$OUT_DIR/cli.raw.txt"

if [[ "$EXPORT_ONLY" -eq 1 ]]; then
  echo "yonko: export-only → $BRIEF"
  exit 0
fi

resolve_claude() {
  if command -v claude >/dev/null 2>&1; then
    command -v claude
    return 0
  fi
  return 1
}

resolve_codex() {
  if command -v codex >/dev/null 2>&1; then
    command -v codex
    return 0
  fi
  if command -v npx >/dev/null 2>&1; then
    echo "npx_codex"
    return 0
  fi
  return 1
}

run_claude() {
  local claude_bin="$1"
  # Paths only - never inline SEAT.md / packet (CLI scaffolding + duplicate text burns tokens).
  local prompt
  prompt="You are a Yonko external reviewer. Read ONLY these files (do not ask for a smoke test):
1) $BRIEF
2) the slim packet path named inside that brief
Verify the slim packet hash, then write findings JSON to: $OUT_JSON
Return ONLY the machine JSON wrapper from the brief."

  set +e
  "$claude_bin" -p "$prompt" \
    --output-format json \
    --add-dir "$OUT_DIR" \
    >"$RAW_OUT" 2>"$OUT_DIR/cli.stderr.txt"
  local rc=$?
  set -e
  return $rc
}

run_codex() {
  local codex_bin="$1"
  local prompt
  prompt="You are a Yonko external reviewer. Read ONLY these files (do not ask for a smoke test):
1) $BRIEF
2) the slim packet path named inside that brief
Verify the slim packet hash, then write findings JSON to: $OUT_JSON
Return ONLY the machine JSON wrapper from the brief."

  # Codex refuses non-git cwd unless --skip-git-repo-check (session external/ is not a repo).
  set +e
  if [[ "$codex_bin" == "npx_codex" ]]; then
    npx --yes @openai/codex exec -C "$OUT_DIR" -s read-only --skip-git-repo-check \
      "$prompt" >"$RAW_OUT" 2>"$OUT_DIR/cli.stderr.txt"
  else
    "$codex_bin" exec -C "$OUT_DIR" -s read-only --skip-git-repo-check \
      "$prompt" >"$RAW_OUT" 2>"$OUT_DIR/cli.stderr.txt"
  fi
  local rc=$?
  set -e
  return $rc
}

extract_findings() {
  python3 - "$OUT_JSON" "$RAW_OUT" "$BRIEF" <<'PY'
import json, re, sys, pathlib
out_path = pathlib.Path(sys.argv[1])
raw_path = pathlib.Path(sys.argv[2])

def try_load(text: str):
    text = text.strip()
    if not text:
        return None
    # direct JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fenced block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # first balanced object with findings
    idx = text.find("{")
    while idx != -1:
        depth = 0
        for i in range(idx, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[idx : i + 1]
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict) and ("findings" in obj or "result" in obj):
                            return obj
                    except json.JSONDecodeError:
                        break
        idx = text.find("{", idx + 1)
    return None

# Prefer file written by the model
if out_path.is_file() and out_path.stat().st_size > 0:
    try:
        obj = json.loads(out_path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "findings" in obj:
            print(json.dumps({"ok": True, "source": "findings.json"}))
            raise SystemExit(0)
    except json.JSONDecodeError:
        pass

raw = raw_path.read_text(encoding="utf-8", errors="replace") if raw_path.exists() else ""
# Claude --output-format json often wraps { "result": "..." }
try:
    wrapper = json.loads(raw)
    if isinstance(wrapper, dict) and "result" in wrapper:
        inner = try_load(str(wrapper["result"]))
        if inner and "findings" in inner:
            out_path.write_text(json.dumps(inner, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"ok": True, "source": "claude_result"}))
            raise SystemExit(0)
except json.JSONDecodeError:
    pass

obj = try_load(raw)
if obj and "findings" in obj:
    out_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "source": "raw_text"}))
    raise SystemExit(0)

print(json.dumps({"ok": False, "error": "could_not_extract_findings_json"}))
raise SystemExit(1)
PY
}

CLI=""
case "$SEAT" in
  blackbeard)
    if [[ -n "$FORCE_CLI" && "$FORCE_CLI" != "claude" ]]; then
      yonko_die "blackbeard expects claude"
    fi
    if ! CLI="$(resolve_claude)"; then
      echo "yonko: claude CLI not found - brief ready at $BRIEF" >&2
      echo "yonko: run Claude Code on SEAT.md then save JSON to $OUT_JSON" >&2
      exit 2
    fi
    echo "yonko: running Blackbeard via $CLI …"
    if ! run_claude "$CLI"; then
      echo "yonko: claude CLI failed - see $OUT_DIR/cli.stderr.txt" >&2
      echo "yonko: brief left at $BRIEF for manual/company Claude Code" >&2
      exit 2
    fi
    ;;
  shanks)
    if [[ -n "$FORCE_CLI" && "$FORCE_CLI" != "codex" ]]; then
      yonko_die "shanks expects codex"
    fi
    if ! CLI="$(resolve_codex)"; then
      echo "yonko: codex CLI not found - brief ready at $BRIEF" >&2
      echo "yonko: install Codex or run manually on SEAT.md → $OUT_JSON" >&2
      exit 2
    fi
    echo "yonko: running Shanks via $CLI …"
    if ! run_codex "$CLI"; then
      echo "yonko: codex CLI failed - see $OUT_DIR/cli.stderr.txt" >&2
      echo "yonko: brief left at $BRIEF for manual Codex" >&2
      exit 2
    fi
    ;;
  *)
    yonko_die "unsupported seat for external run: $SEAT"
    ;;
esac

if ! extract_findings; then
  echo "yonko: CLI ran but findings JSON not extracted - check $RAW_OUT" >&2
  echo "yonko: paste/fix $OUT_JSON manually from CLI output" >&2
  exit 2
fi

# Soft-validate shape (implementation findings kind may differ; still useful)
if [[ -x "$SCRIPT_DIR/validate-artifact.sh" ]]; then
  "$SCRIPT_DIR/validate-artifact.sh" --kind findings --file "$OUT_JSON" >/dev/null 2>&1 || true
fi

"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type external_seat_completed --data "{\"seat\":\"$SEAT\",\"output\":\"$OUT_JSON\",\"cli\":\"$CLI\"}" 2>/dev/null || true

echo "yonko: external seat ok → $OUT_JSON"
python3 -c 'import json; print(json.dumps({"ok": True, "seat": "'"$SEAT"'", "findings": "'"$OUT_JSON"'"}, indent=2))'
