#!/usr/bin/env python3
"""Replay an eval case under frozen_packet or full_pipeline isolation.

Never mutates skill config/execution-profile.json.
Writes under evals/results/<run_id>/ only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.capture import capture_session_observability  # noqa: E402
from lib.evaluation.facts import load_json  # noqa: E402
from lib.evaluation.io import write_json, write_text  # noqa: E402

PROFILE_PATH = SKILL / "config" / "execution-profile.json"


def profile_fingerprint() -> str:
    if not PROFILE_PATH.is_file():
        return "missing"
    return hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument(
        "--mode",
        required=True,
        choices=("frozen_packet", "full_pipeline"),
    )
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    case_path = SKILL / "evals" / "cases" / f"{args.case_id}.json"
    if not case_path.is_file():
        print(json.dumps({"ok": False, "error": "case_not_found", "path": str(case_path)}))
        return 2
    case = load_json(case_path) or {}
    source = Path(str(case.get("source_session_path") or "")).expanduser()
    if not source.is_dir():
        # Fall back to sessions root by id
        from lib.evaluation.config import sessions_root

        source = sessions_root() / str(case.get("source_session_id") or "")
    if not source.is_dir():
        print(json.dumps({"ok": False, "error": "source_session_missing"}))
        return 2

    fp_before = profile_fingerprint()
    run_id = args.run_id or (
        f"{args.case_id}-{args.mode}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    out = SKILL / "evals" / "results" / run_id
    if out.exists():
        print(json.dumps({"ok": False, "error": "run_exists", "path": str(out)}))
        return 5
    out.mkdir(parents=True)

    # Isolation: copy relevant artefacts into run dir; never call set-execution-profile
    work = out / "workspace"
    work.mkdir()
    for name in (
        "session.json",
        "metrics.json",
        "confidence.json",
        "outcome.json",
        "findings.json",
        "packet.md",
        "packet.meta.json",
        "review-quality-human.json",
    ):
        src = source / name
        if src.is_file():
            shutil.copy2(src, work / name)
    for sub in ("evidence", "runtime", "verification.json", "verifications.json"):
        src = source / sub
        if src.is_file():
            shutil.copy2(src, work / sub)
        elif src.is_dir():
            shutil.copytree(src, work / sub)

    # Optional profile/model overrides live ONLY under results
    overrides = {
        "note": "replay isolation - do not mutate skill config/execution-profile.json",
        "replay_mode": args.mode,
        "review_mode": (
            "packet_only"
            if args.mode == "frozen_packet"
            else "packet_plus_workspace_read"
        ),
        "forbid_set_execution_profile": True,
    }
    write_json(out / "execution-overrides.json", overrides)
    isolated_session_path = work / "session.json"
    if isolated_session_path.is_file():
        isolated_session = load_json(isolated_session_path) or {}
        frozen_profile = isolated_session.get("execution_profile") or {}
        for seat in frozen_profile.get("seats") or []:
            if seat.get("runtime") == "opencode":
                seat["review_mode"] = overrides["review_mode"]
        write_json(isolated_session_path, isolated_session)

    expected_hash = str(case.get("packet_hash") or "").lower()
    meta = load_json(work / "packet.meta.json") or {}
    actual_hash = str(meta.get("packet_hash") or "").lower()

    if args.mode == "frozen_packet":
        if not expected_hash or actual_hash != expected_hash:
            write_json(
                out / "eval-run.json",
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "case_id": args.case_id,
                    "replay_mode": args.mode,
                    "ok": False,
                    "error": "frozen_packet_hash_mismatch",
                    "expected": expected_hash,
                    "actual": actual_hash,
                },
            )
            print(json.dumps({"ok": False, "error": "frozen_packet_hash_mismatch"}))
            fp_after = profile_fingerprint()
            if fp_after != fp_before:
                print(json.dumps({"ok": False, "error": "execution_profile_mutated"}))
                return 1
            return 3
        packet_hash = actual_hash
    else:
        # full_pipeline: new hash allowed (measurement may recompute from copied packet)
        packet_hash = actual_hash or expected_hash

    # Re-capture measurement in isolated workspace (observational)
    cap = capture_session_observability(work, write=True, upsert_index=False)
    measurement = cap["measurement"]

    fp_after = profile_fingerprint()
    profile_ok = fp_before == fp_after

    run = {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": args.case_id,
        "replay_mode": args.mode,
        "packet_hash": packet_hash,
        "source_session_id": case.get("source_session_id"),
        "profile_fingerprint_before": fp_before,
        "profile_fingerprint_after": fp_after,
        "profile_unchanged": profile_ok,
        "measurement_path": str(work / "evaluation" / "review-measurement.json"),
        "adjudication_state": measurement.get("adjudication_state"),
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": profile_ok,
    }
    write_json(out / "eval-run.json", run)
    write_text(
        out / "README.md",
        f"# Eval run `{run_id}`\n\nMode: `{args.mode}`\nProfile unchanged: `{profile_ok}`\n",
    )

    print(json.dumps(run, indent=2))
    return 0 if profile_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
