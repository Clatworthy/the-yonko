#!/usr/bin/env python3
"""V3.6 continuous improvement smoke tests."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import continuous_improvement as ci  # noqa: E402


def _rec(eid: str, pattern: str, category: str = "data-integrity", n: int = 1) -> dict:
    findings = []
    for i in range(n):
        findings.append(
            {
                "id": f"{eid}-f{i}",
                "category": category,
                "severity": "high",
                "title": f"Guarded delete issue {i}",
                "finding_pattern": pattern,
                "verification": "confirmed",
            }
        )
    return {
        "evidence_id": eid,
        "lifecycle": "canonical",
        "completed_at": f"2026-01-{eid[-2:] if eid[-2:].isdigit() else '01'}T00:00:00Z",
        "findings": {"accepted": findings, "validated": [], "rejected": [], "unresolved": []},
    }


def test_process_signal_at_threshold():
    cfg = ci.load_ci_config(ROOT / "config" / "continuous-improvement.yaml")
    cfg["window_reviews"] = 40
    cfg["min_occurrences"] = 7
    records = [
        _rec(f"e{i:02d}", "guarded-delete-before-eligibility") for i in range(1, 8)
    ]
    # pad with unrelated
    records += [_rec(f"x{i:02d}", "auth-boundary-gap", category="auth") for i in range(1, 4)]
    report = ci.build_report(records, cfg, evidence_repo="/tmp/fake")
    assert report["mutates_protocol"] is False
    keys = {s["pattern_key"] for s in report["suggestions"]}
    assert "guarded-delete-before-eligibility" in keys
    assert all(s["human_decision_required"] for s in report["suggestions"])
    assert all("forbidden_actions" in s for s in report["suggestions"])
    print("PASS process_signal_at_threshold")


def test_isolated_below_threshold():
    cfg = ci.load_ci_config(ROOT / "config" / "continuous-improvement.yaml")
    cfg["min_occurrences"] = 7
    records = [_rec(f"e{i:02d}", "auth-boundary-gap", category="auth") for i in range(1, 4)]
    report = ci.build_report(records, cfg, evidence_repo=None)
    assert report["suggestions"] == []
    assert any(b["pattern_key"] == "auth-boundary-gap" for b in report["below_threshold"])
    print("PASS isolated_below_threshold")


def test_write_refuses_protocol_paths():
    cfg = ci.load_ci_config(ROOT / "config" / "continuous-improvement.yaml")
    report = ci.build_report([], cfg, evidence_repo=None)
    try:
        ci.write_report(report, ROOT / "prompts", cfg)
        raise AssertionError("should refuse prompts/")
    except ValueError as e:
        assert "refusing" in str(e).lower() or "protocol" in str(e).lower()
    print("PASS write_refuses_protocol_paths")


def test_cli_analyze_fixture_repo():
    import subprocess

    td = Path(tempfile.mkdtemp(prefix="yonko-ci-"))
    try:
        records_dir = td / "records" / "2026"
        for i in range(1, 9):
            d = records_dir / f"ev{i:02d}"
            d.mkdir(parents=True)
            (d / "record.json").write_text(
                json.dumps(_rec(f"ev{i:02d}", "sibling-shared-parent-omission")),
                encoding="utf-8",
            )
        out = td / "out"
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "continuous-improvement.py"),
                "analyze",
                "--repo",
                str(td),
                "--out",
                str(out),
                "--min-occurrences",
                "7",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["ok"] is True
        assert data["mutates_protocol"] is False
        assert data["suggestions_count"] >= 1
        assert (out / "ENGINEERING_IMPROVEMENT_SUGGESTIONS.md").exists()
        md = (out / "ENGINEERING_IMPROVEMENT_SUGGESTIONS.md").read_text()
        assert "Human decision required" in md
        assert "will not rewrite" in md.lower() or "never" in md.lower()
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS cli_analyze_fixture_repo")


def main() -> int:
    test_process_signal_at_threshold()
    test_isolated_below_threshold()
    test_write_refuses_protocol_paths()
    test_cli_analyze_fixture_repo()
    print("ALL V3.6 continuous-improvement smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
