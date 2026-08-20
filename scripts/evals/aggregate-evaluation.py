#!/usr/bin/env python3
"""Aggregate evaluation measurements (read-only learning).

Emits insufficient_sample when n < min_sample_n. Never auto-tunes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.config import load_evaluation_yaml, sessions_root  # noqa: E402
from lib.evaluation.facts import load_json  # noqa: E402
from lib.evaluation.io import write_json, write_text  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-root", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--type", default="", help="implementation|plan|document")
    args = ap.parse_args()

    root = Path(args.sessions_root).expanduser() if args.sessions_root else sessions_root()
    cfg = load_evaluation_yaml()
    min_n = int(cfg.get("min_sample_n") or 10)
    type_filter = (args.type or "").strip() or None

    rows = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        m = load_json(d / "evaluation" / "review-measurement.json")
        if not isinstance(m, dict):
            continue
        if type_filter and m.get("review_type") != type_filter:
            continue
        rows.append(m)
        if args.limit and len(rows) >= args.limit:
            break

    n = len(rows)
    insufficient_sample = n < min_n
    states = Counter(str(r.get("adjudication_state")) for r in rows)
    types = Counter(str(r.get("review_type")) for r in rows)
    flags = Counter()
    disp = Counter()
    for r in rows:
        for f in r.get("flags") or []:
            flags[str(f)] += 1
        counts = ((r.get("dispositions") or {}).get("counts") or {})
        for k, v in counts.items():
            disp[str(k)] += int(v or 0)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sample_size": n,
        "min_sample_n": min_n,
        "insufficient_sample": insufficient_sample,
        "strong_claims_allowed": not insufficient_sample,
        "review_types": dict(types),
        "adjudication_states": dict(states),
        "flag_counts": dict(flags),
        "disposition_counts": dict(disp),
        "policy": "observational_never_auto_tune",
        "note": (
            "insufficient_sample blocks strong improvement claims"
            if insufficient_sample
            else "sample meets min_sample_n"
        ),
    }

    out_dir = root / "_rollup"
    write_json(out_dir / "evaluation-aggregate.json", report)
    md = f"""# Evaluation aggregate

Generated: `{report['generated_at']}`
Sample size: **{n}** (min_sample_n={min_n})
insufficient_sample: **{insufficient_sample}**
strong_claims_allowed: **{report['strong_claims_allowed']}**

## Review types
{json.dumps(dict(types), indent=2)}

## Adjudication states
{json.dumps(dict(states), indent=2)}

## Disposition counts
{json.dumps(dict(disp), indent=2)}

## Flags
{json.dumps(dict(flags), indent=2)}
"""
    write_text(out_dir / "evaluation-aggregate.md", md)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
