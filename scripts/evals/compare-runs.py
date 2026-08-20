#!/usr/bin/env python3
"""Compare two eval runs. Rejects cross-mode (frozen_packet vs full_pipeline)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.facts import load_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-a", required=True)
    ap.add_argument("--run-b", required=True)
    args = ap.parse_args()

    def load_run(run_id: str) -> dict:
        path = SKILL / "evals" / "results" / run_id / "eval-run.json"
        data = load_json(path)
        if not isinstance(data, dict):
            raise SystemExit(json.dumps({"ok": False, "error": "run_missing", "run": run_id}))
        return data

    a = load_run(args.run_a)
    b = load_run(args.run_b)
    if a.get("replay_mode") != b.get("replay_mode"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "cross_mode_compare_forbidden",
                    "mode_a": a.get("replay_mode"),
                    "mode_b": b.get("replay_mode"),
                    "note": "frozen_packet and full_pipeline are distinct and cannot be compared as the same mode",
                },
                indent=2,
            )
        )
        return 3

    report = {
        "ok": True,
        "replay_mode": a.get("replay_mode"),
        "run_a": args.run_a,
        "run_b": args.run_b,
        "packet_hash_a": a.get("packet_hash"),
        "packet_hash_b": b.get("packet_hash"),
        "packet_hash_equal": a.get("packet_hash") == b.get("packet_hash"),
        "adjudication_state_a": a.get("adjudication_state"),
        "adjudication_state_b": b.get("adjudication_state"),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
