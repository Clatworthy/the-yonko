#!/usr/bin/env bash
# yonko-doctor.sh - validate active execution profile (no secrets, no paid inference)
# Usage: yonko-doctor.sh [--profile ID] [--json] [--refresh-models]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m lib.runtime.doctor "$@"
