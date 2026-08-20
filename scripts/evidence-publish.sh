#!/usr/bin/env bash
# evidence-publish.sh - thin wrapper for /yonko evidence publish
# Delegates to evidence-index.py publish. Never commits or pushes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/evidence-index.py" publish "$@"
