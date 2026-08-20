#!/usr/bin/env python3
"""Phase 0 shadow workflow smoke: invisible observation, idempotency, would_block demos."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "workflow"))

import transition as tr  # noqa: E402
import state as st  # noqa: E402


def run(cmd: list[str], env: dict | None = None, **kw) -> subprocess.CompletedProcess:
    base = {**dict(__import__("os").environ), "YONKO_WORKFLOW_MODE": "shadow"}
    if env:
        base.update(env)
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=base, **kw)


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def init_impl(work: Path, sid: str) -> Path:
    out = run([
        str(SCRIPTS / "init-session.sh"),
        "--id", sid,
        "--mode", "standard",
        "--type", "implementation",
    ], env={"YONKO_SESSIONS_ROOT": str(work), "YONKO_WORKFLOW_MODE": "shadow"})
    session = work / sid
    assert (session / "session.json").exists(), out.stdout + out.stderr
    return session


def test_normal_session_shadow():
    work = Path(tempfile.mkdtemp(prefix="yonko-wf0-"))
    try:
        session = init_impl(work, "wf-normal")
        wf = st.load_workflow(session)
        assert wf["mode"] == "shadow"
        assert wf["current_state"] == "INIT"
        # evidence + risk + packet via minimal files + record-event / sanitise
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        write_json(evid / "repos.json", {"repos": [{
            "label": "demo", "path": "/tmp/demo", "branch": "main",
            "patch": "demo.patch", "secrets_excluded": [],
        }]})
        (evid / "DIFF_MAP.txt").write_text("demo: 1\n", encoding="utf-8")
        (evid / "demo.patch").write_text(
            "diff --git a/A b/A\n--- a/A\n+++ b/A\n@@ -1 +1 @@\n-old\n+new\n",
            encoding="utf-8",
        )
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "evidence_collected", "--data", '{"repo_count":1}'])
        write_json(evid / "risk.json", {
            "risk": "medium", "reviewers": 3, "maximum_subagent_calls": 5,
            "verify_material": True, "reasons": ["test"],
        })
        # patch session risk for classify skip - use record-event risk_classified
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "risk_classified", "--data", '{"risk":"medium"}'])
        docket = session / "docket.md"
        docket.write_text("# Docket\n\nDone when: test.\n", encoding="utf-8")
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "reviewers_seated", "--data", '{"count":3,"seats":["shanks","blackbeard","buggy"]}'])
        write_json(session / "findings.json", [])
        run([str(SCRIPTS / "validate-artifact.sh"), "--kind", "findings",
             "--file", str(session / "findings.json")])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "verification_completed", "--data", '{"verdict":"confirmed"}'])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "scoped_verify", "--data", '{"result":"green"}'])
        run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
             "--verdict", "pass", "--confidence", "high"])
        wf = json.loads((session / "workflow.json").read_text())
        assert wf["current_state"] == "FINALIZED"
        assert (session / "workflow-events.jsonl").exists()
        # Packet hash unchanged by workflow
        meta = json.loads((session / "packet.meta.json").read_text())
        packet = (session / "packet.md").read_bytes()
        import hashlib
        assert hashlib.sha256(packet).hexdigest() == meta["packet_hash"]
        print("PASS normal_session_shadow", "state=", wf["current_state"],
              "would_block=", wf.get("would_block_count"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_illegal_finalize_would_block_but_succeeds():
    work = Path(tempfile.mkdtemp(prefix="yonko-wf0-"))
    try:
        session = init_impl(work, "wf-illegal-fin")
        # Minimal pin so finalize isn't PRECONDITION-only
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        write_json(evid / "repos.json", {"repos": [{
            "label": "demo", "path": "/tmp/demo", "branch": "main",
            "patch": "demo.patch", "secrets_excluded": [],
        }]})
        (evid / "DIFF_MAP.txt").write_text("x\n", encoding="utf-8")
        (evid / "demo.patch").write_text("+x\n", encoding="utf-8")
        write_json(evid / "risk.json", {"risk": "high", "reviewers": 4})
        docket = session / "d.md"
        docket.write_text("# D\n", encoding="utf-8")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "evidence_collected", "--data", "{}"])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "risk_classified", "--data", '{"risk":"high"}'])
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)])
        # Open high finding + no seats + no verify
        write_json(session / "findings.json", [{
            "id": "S1", "reviewer": "shanks", "category": "security",
            "severity": "high", "title": "x", "claim": "x",
            "locus": {"repository": "demo", "path": "A"},
            "evidence": "diff", "reachability": "r", "impact": "i",
            "confidence": "high",
        }])
        before = (session / "packet.md").read_text()
        run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
             "--verdict", "pass", "--confidence", "low"])
        after = (session / "packet.md").read_text()
        assert before == after
        wf = json.loads((session / "workflow.json").read_text())
        assert wf["would_block_count"] >= 1
        events = [
            json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()
        ]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert fin["would_block"] is True
        assert "OPEN_MATERIAL_FINDINGS" in fin["failure_codes"] or "REVIEWER_INCOMPLETE" in fin["failure_codes"] or "VERIFICATION_REQUIRED" in fin["failure_codes"]
        assert (session / "SUMMARY.md").exists()  # finalize still wrote
        print("PASS illegal_finalize_would_block", fin["failure_codes"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_stale_packet_detection():
    work = Path(tempfile.mkdtemp(prefix="yonko-wf0-"))
    try:
        session = init_impl(work, "wf-stale")
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        write_json(evid / "repos.json", {"repos": [{
            "label": "demo", "path": "/tmp/demo", "branch": "main",
            "patch": "demo.patch", "secrets_excluded": [],
        }]})
        (evid / "DIFF_MAP.txt").write_text("x\n", encoding="utf-8")
        (evid / "demo.patch").write_text("+one\n", encoding="utf-8")
        write_json(evid / "risk.json", {"risk": "low", "reviewers": 2})
        docket = session / "d.md"
        docket.write_text("# D\n\noriginal\n", encoding="utf-8")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "evidence_collected", "--data", "{}"])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "risk_classified", "--data", "{}"])
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)])
        # Mutate evidence after pin
        (evid / "demo.patch").write_text("+two changed\n", encoding="utf-8")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "evidence_collected", "--data", '{"repo_count":1}'])
        r = tr.record_transition(session, "seat_reviewers", {"seat_count": 2}, None)
        assert r["would_block"] is True
        assert "PACKET_STALE" in r["failure_codes"]
        print("PASS stale_packet_detection", r["failure_codes"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_human_approval_detection():
    work = Path(tempfile.mkdtemp(prefix="yonko-wf0-"))
    try:
        out = run([
            str(SCRIPTS / "init-session.sh"), "--id", "wf-plan", "--type", "plan", "--mode", "standard",
        ], env={"YONKO_SESSIONS_ROOT": str(work), "YONKO_WORKFLOW_MODE": "shadow"})
        session = work / "wf-plan"
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        write_json(evid / "plan-refs.json", {"repositories_named": [], "sources": [], "recon": False})
        (evid / "plan.md").write_text("# Plan\n\n## Steps\nDo thing.\n", encoding="utf-8")
        write_json(evid / "scope-risk.json", {"risk": "medium", "max_confirmation_rounds": 1, "reviewers": 3})
        docket = session / "pd.md"
        docket.write_text("# Plan docket\n", encoding="utf-8")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "plan_evidence_collected", "--data", "{}"])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "scope_risk_classified", "--data", '{"risk":"medium"}'])
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "reviewers_seated", "--data", '{"count":3}'])
        (session / "PLAN.revised.md").write_text("# revised\n", encoding="utf-8")
        # pass without PLAN.approved.md
        run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
             "--verdict", "pass", "--confidence", "medium"])
        events = [
            json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()
        ]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert fin["would_block"] is True
        assert "HUMAN_APPROVAL_REQUIRED" in fin["failure_codes"]
        print("PASS human_approval_detection")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_idempotency_no_duplicate_inflate():
    work = Path(tempfile.mkdtemp(prefix="yonko-wf0-"))
    try:
        session = init_impl(work, "wf-idem")
        # initialise already recorded by init-session → first call is duplicate
        r0 = tr.record_transition(session, "initialise", {}, None)
        assert r0.get("duplicate") is True
        n_before = len([l for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()])
        wb = json.loads((session / "workflow.json").read_text()).get("would_block_count") or 0
        tr.record_transition(session, "initialise", {}, None)
        tr.record_transition(session, "initialise", {}, None)
        n_after = len([l for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()])
        assert n_after == n_before
        wb2 = json.loads((session / "workflow.json").read_text()).get("would_block_count") or 0
        assert wb2 == wb
        # Distinct mechanical keys still append once each
        r1 = tr.record_transition(session, "collect_evidence", {}, "collect_evidence:test-a")
        r2 = tr.record_transition(session, "collect_evidence", {}, "collect_evidence:test-a")
        assert r1.get("duplicate") is False
        assert r2.get("duplicate") is True
        print("PASS idempotency_no_duplicate_inflate")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_old_session_without_workflow_still_ok():
    work = Path(tempfile.mkdtemp(prefix="yonko-wf0-"))
    try:
        session = work / "legacy"
        session.mkdir()
        write_json(session / "session.json", {
            "version": "3.0.0", "session_id": "legacy",
            "review_type": "implementation", "packet_version": 0,
        })
        (session / "events.jsonl").touch()
        # No workflow.json - efficiency report must not crash
        sys.path.insert(0, str(SCRIPTS / "lib"))
        import efficiency_report as er
        r = er.build_efficiency_report(session)
        assert r.get("workflow") is None
        print("PASS old_session_without_workflow")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    test_idempotency_no_duplicate_inflate()
    test_normal_session_shadow()
    test_illegal_finalize_would_block_but_succeeds()
    test_stale_packet_detection()
    test_human_approval_detection()
    test_old_session_without_workflow_still_ok()
    # existing suites
    for name in ("test-efficiency-phase1-smoke.py", "test-information-preservation-smoke.py"):
        p = SCRIPTS / name
        if p.exists():
            print(f"Running {name}...")
            subprocess.check_call([sys.executable, str(p)], cwd=str(SCRIPTS))
    print("\nAll Phase 0 workflow smokes passed.")


if __name__ == "__main__":
    main()
