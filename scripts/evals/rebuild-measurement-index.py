#!/usr/bin/env python3
"""Rebuild measurement-index.jsonl from **/evaluation/review-measurement.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.index import rebuild_measurement_index  # noqa: E402
from lib.evaluation.config import sessions_root  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-root", default=None)
    args = ap.parse_args()
    root = Path(args.sessions_root).expanduser() if args.sessions_root else sessions_root()
    result = rebuild_measurement_index(root=root)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
