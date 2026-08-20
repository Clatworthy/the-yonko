#!/usr/bin/env python3
"""Record a narrow human legality override.

Usage:
  override.py --session DIR --codes CODE1,CODE2 --reason TEXT --approved-by NAME
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import state as st  # noqa: E402
import transition as tr  # noqa: E402
from guards import CHAIR_ALIASES  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Yonko human legality override")
    p.add_argument("--session", required=True)
    p.add_argument("--codes", required=True, help="Comma-separated failure codes")
    p.add_argument("--reason", required=True)
    p.add_argument("--approved-by", required=True)
    args = p.parse_args()
    session_dir = Path(args.session)
    by = args.approved_by.strip()
    if not by or by.lower() in CHAIR_ALIASES:
        print(json.dumps({
            "ok": False,
            "error": "approved_by must be an identifiable human",
            "failure_codes": ["PRECONDITION_FAILED"],
        }))
        return 2
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes or not args.reason.strip():
        print(json.dumps({"ok": False, "error": "codes and reason required"}))
        return 2
    wf = st.load_workflow(session_dir)
    payload = {
        "codes": codes,
        "reason": args.reason.strip(),
        "approved_by": by,
        "would_block_evidence": list(wf.get("last_failure_codes") or []),
    }
    result = tr.record_transition(session_dir, "human_override_legality", payload, None)
    print(json.dumps(result, separators=(",", ":")))
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
