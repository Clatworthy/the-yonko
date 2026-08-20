#!/usr/bin/env python3
"""Record explicit human approval for plan/document artefacts.

Usage:
  approve.py --session DIR --artifact PLAN.approved.md --approved-by NAME
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
    p = argparse.ArgumentParser(description="Yonko human artifact approval")
    p.add_argument("--session", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--approved-by", required=True)
    args = p.parse_args()
    session_dir = Path(args.session)
    by = args.approved_by.strip()
    if not by or by.lower() in CHAIR_ALIASES:
        print(json.dumps({
            "ok": False,
            "error": "approved_by must be an identifiable human (not Chair/Yonko/system)",
            "failure_codes": ["HUMAN_APPROVAL_REQUIRED"],
        }))
        return 2
    art = session_dir / args.artifact
    if not art.is_file():
        print(json.dumps({"ok": False, "error": f"artifact missing: {args.artifact}"}))
        return 2
    digest = st.sha256_bytes(art.read_bytes())
    payload = {
        "approved_by": by,
        "artifact": args.artifact,
        "artifact_hash": digest,
        "approved_at": st.utc_now(),
    }
    st.approval_path(session_dir).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = tr.record_transition(
        session_dir,
        "human_approve_artifact",
        payload,
        f"human_approve:{args.artifact}:{digest}",
    )
    print(json.dumps(result, separators=(",", ":")))
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
