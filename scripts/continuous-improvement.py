#!/usr/bin/env python3
"""Yonko Continuous Improvement (V3.6) - suggest-only pattern analysis.

Usage:
  continuous-improvement.py analyze [--repo PATH] [--out DIR] [--window N] [--min-occurrences N]

Reads canonical Evidence Index records. Emits Engineering Improvement Suggestions.
Never rewrites protocol, routing, prompts, or workflow config. Never git commit/push.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "lib"))

import continuous_improvement as ci  # noqa: E402


def die(msg: str) -> None:
    print(f"yonko improve: ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def info(msg: str) -> None:
    print(f"yonko improve: {msg}", file=sys.stderr)


def resolve_repo(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("YONKO_EVIDENCE_REPO") or ""
    if env:
        return Path(env).expanduser().resolve()
    # fall back to evidence-index.yaml evidence_repo if set
    cfg_path = ROOT / "config" / "evidence-index.yaml"
    if cfg_path.exists():
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("evidence_repo:"):
                val = line.split(":", 1)[1].strip().strip('"').strip("'")
                if val:
                    return Path(val).expanduser().resolve()
    die("set YONKO_EVIDENCE_REPO or pass --repo (Evidence Index checkout)")


def cmd_analyze(args: argparse.Namespace) -> int:
    cfg_path = ROOT / "config" / "continuous-improvement.yaml"
    if not cfg_path.exists():
        die(f"missing {cfg_path}")
    cfg = ci.load_ci_config(cfg_path)
    if not cfg.get("enabled", True):
        die("continuous improvement disabled in config")

    if args.window:
        cfg["window_reviews"] = int(args.window)
    if args.min_occurrences:
        cfg["min_occurrences"] = int(args.min_occurrences)

    repo = resolve_repo(args.repo)
    if not repo.exists():
        die(f"evidence repo not found: {repo}")

    records = ci.iter_canonical_records(repo)
    report = ci.build_report(records, cfg, evidence_repo=str(repo))

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        stamp = report["generated_at"].replace(":", "").replace("-", "")[:15]
        rel = (cfg.get("output") or {}).get("relative_dir") or "improvements"
        out_dir = repo / rel / stamp

    paths = ci.write_report(report, out_dir, cfg)

    info(f"records_scanned={report['records_scanned']} suggestions={len(report['suggestions'])}")
    info(f"wrote {paths['markdown']}")
    info(f"wrote {paths['json']}")
    info("Protocol was NOT modified. Human decides next steps.")
    if report["suggestions"]:
        info("---")
        for s in report["suggestions"]:
            info(f"PROCESS SIGNAL: {s['pattern_key']} ({s['count']}/{s['window']})")
    else:
        info("No process-level patterns crossed the threshold.")

    print(json.dumps({
        "ok": True,
        "mutates_protocol": False,
        "suggestions_count": len(report["suggestions"]),
        "paths": paths,
        "report": report,
    }, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("analyze", help="Scan Evidence Index and emit suggestions")
    s.add_argument("--repo", help="Evidence Index checkout (default YONKO_EVIDENCE_REPO)")
    s.add_argument("--out", help="Output directory (default <repo>/improvements/<stamp>)")
    s.add_argument("--window", type=int, help="Override window_reviews")
    s.add_argument("--min-occurrences", type=int, help="Override min_occurrences")
    s.set_defaults(func=cmd_analyze)

    args = p.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
