#!/usr/bin/env python3
"""Record human-approved ground truth for a session or eval case (suggest-only corpus)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.facts import load_json  # noqa: E402
from lib.evaluation.io import write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--outcome-type", required=True)
    ap.add_argument("--approved-by", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--finding-id", action="append", default=[])
    args = ap.parse_args()

    if not args.session and not args.case_id:
        print(json.dumps({"ok": False, "error": "need --session or --case-id"}))
        return 2

    doc = {
        "schema_version": 1,
        "session_id": None,
        "case_id": args.case_id,
        "outcome_type": args.outcome_type,
        "approved_by": args.approved_by,
        "notes": args.notes or None,
        "finding_ids": args.finding_id,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if args.session:
        session = Path(args.session).expanduser().resolve()
        m = load_json(session / "evaluation" / "review-measurement.json") or {}
        doc["session_id"] = m.get("session_id") or session.name
        out = session / "evaluation" / "ground-truth.json"
        write_json(out, doc)
        print(json.dumps({"ok": True, "path": str(out)}, indent=2))
        return 0

    out = SKILL / "evals" / "cases" / f"{args.case_id}.ground-truth.json"
    write_json(out, doc)
    print(json.dumps({"ok": True, "path": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
