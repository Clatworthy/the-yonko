#!/usr/bin/env python3
"""Record a human-approved escaped defect linked to a prior session."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.io import write_json  # noqa: E402


FAILURE_CLASSES = (
    "missed_finding",
    "false_accept",
    "false_reject",
    "path_quality_gap",
    "verifier_gap",
    "other",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--escaped-defect-id", required=True)
    ap.add_argument("--source-session-id", required=True)
    ap.add_argument("--failure-classification", required=True, choices=FAILURE_CLASSES)
    ap.add_argument("--human-approved-by", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    out_dir = SKILL / "evals" / "escaped-defects"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.escaped_defect_id}.json"
    if path.exists() and not args.overwrite:
        print(json.dumps({"ok": False, "error": "exists", "path": str(path)}))
        return 5

    doc = {
        "schema_version": 1,
        "escaped_defect_id": args.escaped_defect_id,
        "source_session_id": args.source_session_id,
        "failure_classification": args.failure_classification,
        "human_approved_by": args.human_approved_by,
        "notes": args.notes or None,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_json(path, doc)
    print(json.dumps({"ok": True, "path": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
