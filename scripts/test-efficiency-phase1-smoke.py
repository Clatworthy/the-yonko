#!/usr/bin/env python3
"""V4 Phase 1 smoke: efficiency report, linked-plan handoff, dedupe, fail-open."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "lib"))

import assemble_packet  # noqa: E402
import efficiency_report  # noqa: E402
import packet_ops  # noqa: E402


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def base_impl_session(parent: Path, sid: str, linked: str | None = None) -> Path:
    s = parent / sid
    (s / "evidence").mkdir(parents=True)
    write_json(
        s / "session.json",
        {
            "version": "3.0.0",
            "session_id": sid,
            "review_type": "implementation",
            "linked_session": linked,
            "packet_version": 0,
            "status": "initialized",
            "risk": "medium",
        },
    )
    write_json(
        s / "evidence" / "repos.json",
        {
            "repos": [
                {
                    "label": "demo-svc",
                    "path": "/tmp/demo",
                    "branch": "main",
                    "patch": "demo.patch",
                    "secrets_excluded": [],
                }
            ]
        },
    )
    (s / "evidence" / "DIFF_MAP.txt").write_text("demo-svc: 1 file\n", encoding="utf-8")
    patch = (
        "diff --git a/A.java b/A.java\n"
        "--- a/A.java\n+++ b/A.java\n"
        "@@ -1 +1 @@\n-old\n+new unique patch body that is long enough xyz\n"
    )
    (s / "evidence" / "demo.patch").write_text(patch, encoding="utf-8")
    return s


def test_dedupe_never_touches_diff():
    dup = "Z" * 140
    packet = (
        f"=== YONKO DOCKET ===\n\n{dup}\n\n"
        f"=== APPROVED PLAN (linked) ===\n\n{dup}\n\n"
        f"=== DIFF: demo-svc ===\n\n{dup}\n\n"
    )
    out, receipt = packet_ops.dedupe_packet(packet)
    assert "[dedup:ref=1 source=YONKO DOCKET]" in out
    # DIFF retains full duplicate
    assert out.split("=== DIFF: demo-svc ===")[1].count(dup) == 1
    assert receipt["replacements"]
    print("PASS dedupe_never_touches_diff")


def test_dedupe_skips_fences_and_unique():
    unique_a = ("ALPHA-" * 30)  # >120
    unique_b = ("BETA--" * 30)
    fence = "```\n" + unique_a + "\n```"
    packet = (
        f"=== A ===\n\n{unique_a}\n\n"
        f"=== B ===\n\n{fence}\n\n"
        f"=== C ===\n\n{unique_b}\n\n"
    )
    out, receipt = packet_ops.dedupe_packet(packet)
    assert unique_a in out  # first keep
    assert unique_b in out
    assert fence in out or ("```\n" + unique_a) in out
    assert receipt["replacements"] == []
    print("PASS dedupe_skips_fences_and_unique")


def test_linked_handoff_excludes_noise():
    work = Path(tempfile.mkdtemp(prefix="yonko-p1-"))
    try:
        plan = work / "plan-sess"
        plan.mkdir()
        write_json(plan / "session.json", {"review_type": "plan", "session_id": "plan-sess"})
        plan_body = (
            "# Approved plan\n\n"
            "## Decisions\nUse approach X for compatibility.\n\n"
            "## Known risks\nSibling parent case.\n\n"
            "## Required verification\nUnit test for empty and non-empty.\n\n"
            + ("Requirement text that should not be duplicated elsewhere. " * 8)
            + "\n"
        )
        (plan / "PLAN.approved.md").write_text(plan_body, encoding="utf-8")
        # Noise that must NOT appear in packet
        (plan / "findings.json").write_text('[{"id":"noise"}]', encoding="utf-8")
        (plan / "packet.md").write_text("=== PRIOR PACKET NOISE ===\nSHOULD_NOT_APPEAR\n", encoding="utf-8")

        impl = base_impl_session(work, "impl-sess", linked=str(plan))
        # Put same long requirement into docket to exercise dedupe
        req = ("Requirement text that should not be duplicated elsewhere. " * 8).strip()
        docket = impl / "DOCKET_SRC.md"
        docket.write_text(
            f"# Docket\n\nDone when: ship safely.\n\n{req}\n\nDeviations: none.\n",
            encoding="utf-8",
        )
        meta = assemble_packet.assemble(impl, docket, "implementation")
        packet = (impl / "packet.md").read_text(encoding="utf-8")
        assert "=== APPROVED PLAN (linked) ===" in packet
        assert "SHOULD_NOT_APPEAR" not in packet
        assert '"id":"noise"' not in packet
        assert (impl / "evidence" / "approved-plan.md").exists()
        assert meta.get("linked_plan_handoff")
        assert "findings.json" in meta["linked_plan_handoff"]["excluded"]
        # hash matches file
        digest = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        assert meta["packet_hash"] == digest
        assert "deduplication" in meta
        print("PASS linked_handoff_excludes_noise")
        print("  packet bytes", meta["bytes"], "dedupe saved est", meta["deduplication"]["bytes_saved_estimate"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_no_linked_keeps_structure():
    work = Path(tempfile.mkdtemp(prefix="yonko-p1-"))
    try:
        impl = base_impl_session(work, "impl-nolink", linked=None)
        docket = impl / "d.md"
        docket.write_text("# Docket\n\nSimple.\n", encoding="utf-8")
        meta = assemble_packet.assemble(impl, docket, "implementation")
        packet = (impl / "packet.md").read_text(encoding="utf-8")
        assert "=== APPROVED PLAN (linked) ===" not in packet
        assert "linked_plan_handoff" not in meta
        assert "=== DIFF: demo-svc ===" in packet
        print("PASS no_linked_keeps_structure")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_efficiency_report_fail_open_and_content():
    work = Path(tempfile.mkdtemp(prefix="yonko-p1-"))
    try:
        impl = base_impl_session(work, "impl-eff", linked=None)
        docket = impl / "d.md"
        docket.write_text("# Docket\n\n" + ("context " * 40) + "\n", encoding="utf-8")
        assemble_packet.assemble(impl, docket, "implementation")
        write_json(
            impl / "metrics.json",
            {
                "review_type": "implementation",
                "risk": "medium",
                "task_calls": 3,
                "duration_seconds": 12,
                "verification": {"confirmed": 1, "rejected": 0},
            },
        )
        (impl / "SUMMARY.md").write_text("# Summary\n\nok\n", encoding="utf-8")
        (impl / "findings.json").write_text("[]\n", encoding="utf-8")
        report = efficiency_report.write_efficiency_report(impl)
        assert report["observational_only"] is True
        assert "Observational only" in report["disclaimer"]
        assert (impl / "efficiency-report.json").exists()
        summary = (impl / "SUMMARY.md").read_text(encoding="utf-8")
        assert "## Engineering Efficiency Report" in summary
        assert "No optimisation performed" in summary
        print("PASS efficiency_report_content")
        print("  est packet tokens", report["packet"]["estimated_tokens"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_efficiency_fail_open_on_finalize_error():
    """Reporting exception must not raise from write if caller wraps; module itself ok."""
    # Call with empty dir - should still return a report structure or raise;
    # finalize wraps in try/except. Here we assert missing packet yields zeros.
    work = Path(tempfile.mkdtemp(prefix="yonko-p1-"))
    try:
        write_json(work / "session.json", {"session_id": "x", "review_type": "implementation"})
        (work / "SUMMARY.md").write_text("# S\n", encoding="utf-8")
        r = efficiency_report.write_efficiency_report(work)
        assert r["packet"]["estimated_tokens"] == 0
        print("PASS efficiency_empty_session")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_before_after_estimates():
    """Representative fixture token estimates before/after structural dedupe."""
    ticket = ("Ticket AC: the system must reject invalid presence shapes. " * 10).strip()
    plan = (
        "# PLAN.approved\n\n## Decisions\nShip model first.\n\n## Risks\nTOCTOU on delete.\n\n"
        + ticket
        + "\n"
    )
    docket = "# Docket\n\n## Intent\nImplement safely.\n\n" + ticket + "\n"
    before = f"=== YONKO DOCKET ===\n\n{docket}\n\n=== APPROVED PLAN (linked) ===\n\n{plan}\n"
    after, receipt = packet_ops.dedupe_packet(before)
    b_tok = efficiency_report.est_tokens(before)
    a_tok = efficiency_report.est_tokens(after)
    saved = b_tok - a_tok
    print("PASS before_after_estimates")
    print(f"  before~{b_tok} after~{a_tok} saved~{saved} (estimate; receipt bytes {receipt['bytes_saved_estimate']})")
    assert saved >= 0
    assert receipt["bytes_saved_estimate"] >= 0


def test_prompt_slim_markers():
    for name in ("reviewers.md", "plan-reviewers.md", "document-reviewers.md"):
        text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
        assert "Chair-only" in text or "Material findings only" in text
        assert "Forbidden in seat output" in text or "Material findings only" in text
    print("PASS prompt_slim_markers")


def main():
    test_dedupe_never_touches_diff()
    test_dedupe_skips_fences_and_unique()
    test_linked_handoff_excludes_noise()
    test_no_linked_keeps_structure()
    test_efficiency_report_fail_open_and_content()
    test_efficiency_fail_open_on_finalize_error()
    test_before_after_estimates()
    test_prompt_slim_markers()
    # information preservation mechanical AC
    ip_smoke = SCRIPTS / "test-information-preservation-smoke.py"
    if ip_smoke.exists():
        print("Running information-preservation smoke...")
        subprocess.check_call([sys.executable, str(ip_smoke)], cwd=str(SCRIPTS))
    # evidence index smoke still passes (compat)
    ev = SCRIPTS / "test-evidence-index-smoke.py"
    if ev.exists():
        print("Running evidence-index smoke for compat...")
        subprocess.check_call([sys.executable, str(ev)], cwd=str(SCRIPTS))
    print("\nAll V4 Phase 1 smokes passed.")


if __name__ == "__main__":
    main()
