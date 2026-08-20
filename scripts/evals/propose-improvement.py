#!/usr/bin/env python3
"""Suggest-only improvement proposals. Never edits prompts/routing/models/policies/code."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.config import load_evaluation_yaml, sessions_root  # noqa: E402
from lib.evaluation.facts import load_json  # noqa: E402
from lib.evaluation.io import write_json  # noqa: E402

FORBIDDEN_EDIT_TARGETS = (
    "prompts",
    "routing",
    "models",
    "model-selections",
    "execution-profile",
    "policies",
    "code",
    "SKILL.md",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal-id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--sessions-root", default=None)
    ap.add_argument("--claim", default="")
    ap.add_argument("--strong-claim", action="store_true")
    args = ap.parse_args()

    cfg = load_evaluation_yaml()
    min_n = int(cfg.get("min_sample_n") or 10)
    root = Path(args.sessions_root).expanduser() if args.sessions_root else sessions_root()

    session_ids = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if (d / "evaluation" / "review-measurement.json").is_file():
            session_ids.append(d.name)

    n = len(session_ids)
    insufficient_sample = n < min_n

    if args.strong_claim and insufficient_sample:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "insufficient_sample",
                    "sample_size": n,
                    "min_sample_n": min_n,
                    "insufficient_sample": True,
                    "note": "strong improvement claims blocked when sample_size < min_sample_n",
                },
                indent=2,
            )
        )
        return 3

    agg = load_json(root / "_rollup" / "evaluation-aggregate.json") or {}
    if agg.get("insufficient_sample") and args.strong_claim:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "insufficient_sample",
                    "from": "evaluation-aggregate.json",
                    "insufficient_sample": True,
                },
                indent=2,
            )
        )
        return 3

    proposal = {
        "schema_version": 1,
        "proposal_id": args.proposal_id,
        "title": args.title,
        "status": "proposed",
        "sample_size": n,
        "min_sample_n": min_n,
        "insufficient_sample": insufficient_sample,
        "evidence_session_ids": session_ids[:50],
        "claim": args.claim or None,
        "strong_claim": bool(args.strong_claim),
        "suggest_only": True,
        "forbidden_edit_targets": list(FORBIDDEN_EDIT_TARGETS),
        "may_edit": [],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": "suggest_only_never_auto_apply",
        "note": "Human must apply any change outside this proposal file. Yonko will not edit prompts/routing/models/policies/code.",
    }

    out_dir = SKILL / "improvements" / "candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.proposal_id}.json"
    write_json(path, proposal)
    print(json.dumps({"ok": True, "path": str(path), "insufficient_sample": insufficient_sample}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
