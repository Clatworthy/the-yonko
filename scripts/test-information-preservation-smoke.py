#!/usr/bin/env python3
"""Prove Phase 1 structural opts preserve engineering information (mechanical AC)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "lib"))

import information_preservation as ip  # noqa: E402
import packet_ops  # noqa: E402


def test_dedupe_preserves_diff_and_code():
    code = "void ship() {\n  // unique engineering\n  rehomeNotes();\n}\n"
    diff_body = (
        "diff --git a/X.java b/X.java\n"
        "--- a/X.java\n+++ b/X.java\n"
        "@@ -1,3 +1,5 @@\n"
        + code
    )
    prose = ("This requirement paragraph is repeated across docket and plan. " * 6).strip()
    original = (
        f"=== YONKO DOCKET ===\n\n{prose}\n\n"
        f"=== APPROVED PLAN (linked) ===\n\n{prose}\n\n"
        f"=== NOTES ===\n\n```\n{code}```\n\n"
        f"=== DIFF: svc ===\n\n{diff_body}\n"
    )
    optimised, receipt = packet_ops.dedupe_packet(original)
    assert receipt["replacements"], "expected prose dedupe"
    result = ip.compare_packets(original, optimised)
    assert result.ok, result.errors
    assert result.diff_bodies_identical
    assert result.fenced_blocks_identical
    assert result.bytes_saved > 0
    assert "[dedup:ref=1 source=YONKO DOCKET]" in optimised
    assert diff_body in optimised
    print("PASS dedupe_preserves_diff_and_code", f"saved~{result.estimated_token_saved} tok est")


def test_compare_script_mechanical():
    with tempfile.TemporaryDirectory(prefix="yonko-ip-") as td:
        td = Path(td)
        prose = ("Deploy order must remain verbatim when unique. " * 8).strip()
        # unique deploy order - should NOT be removed
        deploy = (
            "1. Elevate model MR to Beta.\n"
            "2. Wait for jar tag.\n"
            "3. Bump lockfile.\n"
            "4. Elevate service MR.\n"
        )
        original = (
            f"=== YONKO DOCKET ===\n\n{prose}\n\n## Deploy\n{deploy}\n\n"
            f"=== OTHER ===\n\n{prose}\n\n"
            f"=== DIFF: a ===\n\n+line unique patch content here\n"
        )
        optimised, _ = packet_ops.dedupe_packet(original)
        (td / "o.md").write_text(original, encoding="utf-8")
        (td / "n.md").write_text(optimised, encoding="utf-8")
        out = td / "out"
        subprocess.check_call(
            [
                str(SCRIPTS / "compare-optimisation-quality.sh"),
                "--original",
                str(td / "o.md"),
                "--optimised",
                str(td / "n.md"),
                "--out",
                str(out),
            ]
        )
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["mechanical_ok"] is True
        assert deploy in optimised  # unique deploy ordering preserved
        print("PASS compare_script_mechanical")


def test_council_compare_rejects_weaker_unjustified():
    rec = ip.CouncilCompareRecord(
        fixture_id="t1",
        material_findings_original=3,
        material_findings_optimised=1,
        justified_differences=[],
        confidence_original="high",
        confidence_optimised="medium",
    )
    assert rec.evaluate() is False
    assert rec.unexplained_differences
    rec2 = ip.CouncilCompareRecord(
        fixture_id="t2",
        material_findings_original=3,
        material_findings_optimised=2,
        finding_categories_original=["concurrency", "security", "testing"],
        finding_categories_optimised=["concurrency", "security"],
        justified_differences=[
            "Finding F3 was a duplicate of F1 after dedupe refs resolved to same locus; "
            "testing coverage retained via F1"
        ],
        confidence_original="high",
        confidence_optimised="high",
        verifier_weakened=False,
    )
    assert rec2.evaluate() is True
    print("PASS council_compare_gate")


def test_council_compare_rejects_coverage_swap_same_count():
    """Same finding count with lost security class must fail."""
    rec = ip.CouncilCompareRecord(
        fixture_id="coverage-swap",
        material_findings_original=2,
        material_findings_optimised=2,
        finding_categories_original=["concurrency", "security"],
        finding_categories_optimised=["concurrency", "documentation"],
        justified_differences=[],
        confidence_original="high",
        confidence_optimised="high",
        verifier_weakened=False,
    )
    assert rec.evaluate() is False
    assert any("coverage" in e for e in rec.unexplained_differences)
    assert "security" in rec.coverage_lost
    # Justified coverage loss may pass
    rec_ok = ip.CouncilCompareRecord(
        fixture_id="coverage-justified",
        material_findings_original=2,
        material_findings_optimised=2,
        finding_categories_original=["concurrency", "security"],
        finding_categories_optimised=["concurrency"],
        justified_differences=[
            "security finding was a false positive on both packets; coverage drop intentional"
        ],
        confidence_original="high",
        confidence_optimised="high",
        verifier_weakened=False,
    )
    assert rec_ok.evaluate() is True
    print("PASS council_compare_coverage")


def test_altered_diff_fails():
    o = "=== DIFF: a ===\n\n+hello world unique\n"
    n = "=== DIFF: a ===\n\n+hello world SUMMARISED\n"
    r = ip.compare_packets(o, n)
    assert r.ok is False
    assert r.altered_diff_labels == ["a"]
    print("PASS altered_diff_fails")


def main():
    test_dedupe_preserves_diff_and_code()
    test_compare_script_mechanical()
    test_council_compare_rejects_weaker_unjustified()
    test_council_compare_rejects_coverage_swap_same_count()
    test_altered_diff_fails()
    print("\nAll information-preservation smokes passed.")


if __name__ == "__main__":
    main()
