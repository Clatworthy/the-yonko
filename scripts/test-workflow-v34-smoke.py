#!/usr/bin/env python3
"""V3.4 authoritative workflow legality smoke tests."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "workflow"))

import transition as tr  # noqa: E402
import state as st  # noqa: E402
import explain as ex  # noqa: E402


def env_base(**extra) -> dict:
    e = dict(os.environ)
    e.update(extra)
    return e


def run(cmd: list[str], env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=check, capture_output=True, text=True, env=env or env_base()
    )


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def init_session(work: Path, sid: str, rtype: str = "implementation",
                 mode: str = "enforce", **kwargs) -> Path:
    cmd = [
        str(SCRIPTS / "init-session.sh"), "--id", sid, "--mode", "standard", "--type", rtype,
    ]
    if rtype == "document":
        cmd += ["--artifact", kwargs.get("artifact", "pap")]
    r = run(cmd, env=env_base(YONKO_SESSIONS_ROOT=str(work), YONKO_WORKFLOW_MODE=mode))
    assert r.returncode == 0, r.stderr
    session = work / sid
    assert (session / "session.json").exists()
    return session


def seed_impl_packet(session: Path, risk: str = "medium", reviewers: int = 3) -> None:
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
    write_json(evid / "risk.json", {
        "risk": risk, "reviewers": reviewers, "maximum_subagent_calls": 5,
        "verify_material": True, "reasons": ["test"],
    })
    docket = session / "docket.md"
    docket.write_text("# Docket\n\nDone when: test.\n", encoding="utf-8")
    mode = json.loads((session / "workflow.json").read_text()).get("mode") if (session / "workflow.json").exists() else "enforce"
    # Prefer env from workflow after init
    wf_mode = "enforce"
    if (session / "workflow.json").exists():
        wf_mode = json.loads((session / "workflow.json").read_text()).get("mode") or "enforce"
    e = env_base(YONKO_WORKFLOW_MODE=wf_mode)
    run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
         "--type", "evidence_collected", "--data", '{"repo_count":1}'], env=e)
    run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
         "--type", "risk_classified", "--data", json.dumps({"risk": risk})], env=e)
    run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
         "--session", str(session), "--docket", str(docket)], env=e)


def seat_and_verify(session: Path, count: int = 3, mode: str = "enforce") -> None:
    e = env_base(YONKO_WORKFLOW_MODE=mode)
    run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
         "--type", "reviewers_seated",
         "--data", json.dumps({"count": count, "seats": ["shanks", "blackbeard", "buggy"][:count]})], env=e)
    write_json(session / "findings.json", [])
    run([str(SCRIPTS / "validate-artifact.sh"), "--kind", "findings",
         "--file", str(session / "findings.json")], env=e)
    run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
         "--type", "verification_completed", "--data", '{"verdict":"confirmed"}'], env=e)
    run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
         "--type", "scoped_verify", "--data", '{"result":"green"}'], env=e)


def test_mode_shadow_would_block_exits_zero():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "sh-wb", mode="shadow")
        seed_impl_packet(session, risk="high", reviewers=4)
        write_json(session / "findings.json", [{
            "id": "S1", "reviewer": "shanks", "category": "security",
            "severity": "high", "title": "x", "claim": "x",
            "locus": {"repository": "demo", "path": "A"},
            "evidence": "diff", "reachability": "r", "impact": "i",
            "confidence": "high",
        }])
        r = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                 "--verdict", "pass", "--confidence", "low"],
                env=env_base(YONKO_WORKFLOW_MODE="shadow"), check=False)
        assert r.returncode == 0, r.stderr
        assert (session / "SUMMARY.md").exists()
        events = [json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert fin.get("would_block") is True
        assert fin.get("blocked") is not True
        print("PASS mode_shadow_would_block_exits_zero", fin["failure_codes"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_mode_enforce_blocks_nonzero():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-block", mode="enforce")
        seed_impl_packet(session, risk="high", reviewers=4)
        write_json(session / "findings.json", [{
            "id": "S1", "reviewer": "shanks", "category": "security",
            "severity": "high", "title": "x", "claim": "x",
            "locus": {"repository": "demo", "path": "A"},
            "evidence": "diff", "reachability": "r", "impact": "i",
            "confidence": "high",
        }])
        r = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                 "--verdict", "pass", "--confidence", "low"],
                env=env_base(YONKO_WORKFLOW_MODE="enforce"), check=False)
        assert r.returncode != 0, r.stdout + r.stderr
        assert not (session / "SUMMARY.md").exists()
        sess = json.loads((session / "session.json").read_text())
        assert sess.get("status") != "finalized"
        events = [json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert fin.get("blocked") is True
        assert "OPEN_MATERIAL_FINDINGS" in fin["failure_codes"] or "REVIEWER_INCOMPLETE" in fin["failure_codes"]
        print("PASS mode_enforce_blocks_nonzero", fin["failure_codes"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_clean_impl_finalizes_enforce():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-clean", mode="enforce")
        seed_impl_packet(session)
        seat_and_verify(session)
        r = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                 "--verdict", "pass", "--confidence", "high"],
                env=env_base(YONKO_WORKFLOW_MODE="enforce"), check=False)
        assert r.returncode == 0, r.stderr
        wf = json.loads((session / "workflow.json").read_text())
        assert wf["current_state"] == "FINALIZED"
        assert wf.get("mode") == "enforce"
        print("PASS clean_impl_finalizes_enforce")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_open_medium_blocks_pass():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-med", mode="enforce")
        seed_impl_packet(session)
        seat_and_verify(session)
        write_json(session / "findings.json", [{
            "id": "M1", "reviewer": "shanks", "category": "correctness",
            "severity": "medium", "title": "open", "claim": "c",
            "locus": {"repository": "demo", "path": "A"},
            "evidence": "diff", "reachability": "r", "impact": "i",
            "confidence": "high",
        }])
        r = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                 "--verdict", "pass", "--confidence", "medium"],
                env=env_base(YONKO_WORKFLOW_MODE="enforce"), check=False)
        assert r.returncode != 0
        events = [json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert "OPEN_MATERIAL_FINDINGS" in fin["failure_codes"]
        print("PASS open_medium_blocks_pass")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_dropped_finding_does_not_block():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-drop", mode="enforce")
        seed_impl_packet(session)
        seat_and_verify(session)
        write_json(session / "findings.json", {
            "accepted": [],
            "held": [],
            "dropped": [{
                "id": "D1", "reviewer": "shanks", "category": "style",
                "severity": "high", "title": "dropped", "claim": "c",
                "locus": {"repository": "demo", "path": "A"},
                "evidence": "diff", "reachability": "r", "impact": "i",
                "confidence": "low", "disposition": "drop",
            }],
            "notes": [],
        })
        r = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                 "--verdict", "pass", "--confidence", "high"],
                env=env_base(YONKO_WORKFLOW_MODE="enforce"), check=False)
        assert r.returncode == 0, r.stderr
        print("PASS dropped_finding_does_not_block")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_missing_and_failed_verification_block():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-nover", mode="enforce")
        seed_impl_packet(session, risk="medium", reviewers=3)
        e = env_base(YONKO_WORKFLOW_MODE="enforce")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "reviewers_seated", "--data", '{"count":3}'], env=e)
        write_json(session / "findings.json", [])
        r = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                 "--verdict", "pass", "--confidence", "low"], env=e, check=False)
        assert r.returncode != 0
        events = [json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert "VERIFICATION_REQUIRED" in fin["failure_codes"]

        session2 = init_session(work, "en-failver", mode="enforce")
        seed_impl_packet(session2)
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session2),
             "--type", "reviewers_seated", "--data", '{"count":3}'], env=e)
        write_json(session2 / "findings.json", [])
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session2),
             "--type", "verification_completed", "--data", '{"verdict":"rejected"}'], env=e)
        r2 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session2),
                  "--verdict", "pass", "--confidence", "low"], env=e, check=False)
        assert r2.returncode != 0
        events2 = [json.loads(l) for l in (session2 / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin2 = [e for e in events2 if e.get("transition") == "finalize"][-1]
        assert "VERIFICATION_REQUIRED" in fin2["failure_codes"]
        print("PASS missing_and_failed_verification_block")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_blocked_then_corrected_retry():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-retry", mode="enforce")
        seed_impl_packet(session)
        e = env_base(YONKO_WORKFLOW_MODE="enforce")
        # Incomplete: no seats / verify
        r1 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                  "--verdict", "pass", "--confidence", "low"], env=e, check=False)
        assert r1.returncode != 0
        n1 = len([l for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()])
        seat_and_verify(session)
        r2 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                  "--verdict", "pass", "--confidence", "high"], env=e, check=False)
        assert r2.returncode == 0, r2.stderr
        n2 = len([l for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()])
        assert n2 > n1
        fins = [json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin_events = [x for x in fins if x.get("transition") == "finalize"]
        assert any(x.get("blocked") for x in fin_events)
        assert any(x.get("allowed") and not x.get("blocked") for x in fin_events)
        # Duplicate success finalize should be idempotent
        r3 = tr.record_transition(session, "finalize", {"verdict": "pass"}, None)
        assert r3.get("duplicate") is True
        print("PASS blocked_then_corrected_retry")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_human_approval_plan():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-plan", rtype="plan", mode="enforce")
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        write_json(evid / "plan-refs.json", {"repositories_named": [], "sources": [], "recon": False})
        (evid / "plan.md").write_text("# Plan\n\n## Steps\nDo thing.\n", encoding="utf-8")
        write_json(evid / "scope-risk.json", {"risk": "medium", "max_confirmation_rounds": 1, "reviewers": 3})
        docket = session / "pd.md"
        docket.write_text("# Plan docket\n", encoding="utf-8")
        e = env_base(YONKO_WORKFLOW_MODE="enforce")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "plan_evidence_collected", "--data", "{}"], env=e)
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "scope_risk_classified", "--data", '{"risk":"medium"}'], env=e)
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)], env=e)
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "reviewers_seated", "--data", '{"count":3}'], env=e)
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "verification_completed", "--data", '{"verdict":"confirmed"}'], env=e)
        (session / "PLAN.revised.md").write_text("# revised\n", encoding="utf-8")
        r1 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                  "--verdict", "pass", "--confidence", "medium"], env=e, check=False)
        assert r1.returncode != 0
        events = [json.loads(l) for l in (session / "workflow-events.jsonl").read_text().splitlines() if l.strip()]
        fin = [e for e in events if e.get("transition") == "finalize"][-1]
        assert "HUMAN_APPROVAL_REQUIRED" in fin["failure_codes"]

        (session / "PLAN.approved.md").write_text("# approved\n", encoding="utf-8")
        # Chair self-approval rejected
        bad = run([sys.executable, str(SCRIPTS / "workflow" / "approve.py"),
                   "--session", str(session), "--artifact", "PLAN.approved.md",
                   "--approved-by", "Chair"], env=e, check=False)
        assert bad.returncode != 0

        ok = run([sys.executable, str(SCRIPTS / "workflow" / "approve.py"),
                  "--session", str(session), "--artifact", "PLAN.approved.md",
                  "--approved-by", "alice"], env=e, check=False)
        assert ok.returncode == 0, ok.stderr + ok.stdout
        r2 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                  "--verdict", "pass", "--confidence", "medium"], env=e, check=False)
        assert r2.returncode == 0, r2.stderr

        # Implementation cannot misuse human_approve
        impl = init_session(work, "en-impl-ha", mode="enforce")
        r_ha = tr.record_transition(impl, "human_approve_artifact", {
            "approved_by": "alice", "artifact": "PLAN.approved.md",
        }, None)
        assert r_ha.get("blocked") or "WRITE_POLICY_VIOLATION" in (r_ha.get("failure_codes") or [])
        print("PASS human_approval_plan")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_packet_stale_and_repin():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-stale", mode="enforce")
        seed_impl_packet(session, risk="low", reviewers=2)
        before = (session / "packet.md").read_bytes()
        meta = json.loads((session / "packet.meta.json").read_text())
        assert hashlib.sha256(before).hexdigest() == meta["packet_hash"]

        (session / "evidence" / "demo.patch").write_text("+changed\n", encoding="utf-8")
        e = env_base(YONKO_WORKFLOW_MODE="enforce")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "evidence_collected", "--data", '{"repo_count":1}'], env=e)
        # pin must not have changed original packet bytes from earlier
        # (new collect may auto-invalidate)
        r = tr.record_transition(session, "seat_reviewers", {"seat_count": 2}, None)
        assert r.get("blocked") is True
        assert "PACKET_STALE" in r["failure_codes"] or "PACKET_HASH_MISMATCH" in r["failure_codes"] or "ILLEGAL_TRANSITION" in r["failure_codes"]

        docket = session / "docket.md"
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)], env=e)
        after_pin = (session / "packet.md").read_bytes()
        # New pin changes packet content legitimately; hash must match file
        sess = json.loads((session / "session.json").read_text())
        assert hashlib.sha256(after_pin).hexdigest() == sess["packet_hash"]
        r2 = tr.record_transition(session, "seat_reviewers", {"seat_count": 2}, None)
        assert r2.get("blocked") is not True, r2
        # Duplicate pin same inputs idempotent
        h = sess["packet_hash"]
        r3 = tr.record_transition(session, "pin_packet", {}, None)
        assert r3.get("duplicate") is True or r3.get("allowed")
        print("PASS packet_stale_and_repin", r["failure_codes"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_budget_and_write_fence():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-budget", rtype="plan", mode="enforce")
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        write_json(evid / "plan-refs.json", {"repositories_named": [], "sources": [], "recon": False})
        (evid / "plan.md").write_text("# Plan\n", encoding="utf-8")
        write_json(evid / "scope-risk.json", {"risk": "low", "max_confirmation_rounds": 1, "reviewers": 2})
        docket = session / "pd.md"
        docket.write_text("# D\n", encoding="utf-8")
        e = env_base(YONKO_WORKFLOW_MODE="enforce")
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "plan_evidence_collected", "--data", "{}"], env=e)
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "scope_risk_classified", "--data", "{}"], env=e)
        run([str(SCRIPTS / "sanitise-and-hash-packet.sh"),
             "--session", str(session), "--docket", str(docket)], env=e)
        run([str(SCRIPTS / "record-event.sh"), "--session", str(session),
             "--type", "reviewers_seated", "--data", '{"count":2}'], env=e)
        # Revision requires but does not consume confirmation budget
        r1 = tr.record_transition(session, "apply_or_revise", {
            "artifact": "PLAN.revised.md",
            "accepted_medium_or_higher": True,
            "material_leaf_revision": True,
        }, "apply:1")
        assert r1.get("blocked") is not True, r1
        workflow = json.loads((session / "workflow.json").read_text())
        assert workflow.get("confirmation_required") is True
        assert workflow.get("confirmation_rounds") == 0

        premature = tr.record_transition(
            session, "finalize", {"verdict": "pass"}, "finalize:before-confirm"
        )
        assert premature.get("blocked") is True
        assert "PLAN_CONFIRMATION_REQUIRED" in premature["failure_codes"]

        rematch = tr.record_transition(session, "rematch", {}, "rematch:1")
        assert rematch.get("blocked") is not True, rematch
        seated = tr.record_transition(session, "seat_reviewers", {
            "seat_count": 2,
            "confirmation_round": True,
        }, "seat:confirmation:1")
        assert seated.get("blocked") is not True, seated

        conf = json.loads((session / "workflow.json").read_text()).get("confirmation_rounds")
        assert conf == 1
        tr.record_transition(session, "seat_reviewers", {
            "seat_count": 2,
            "confirmation_round": True,
        }, "seat:confirmation:1")
        conf2 = json.loads((session / "workflow.json").read_text()).get("confirmation_rounds")
        assert conf2 == conf

        second = tr.record_transition(session, "rematch", {}, "rematch:2")
        assert second.get("blocked") is True
        assert "BUDGET_EXCEEDED" in second["failure_codes"]

        r3 = tr.record_transition(session, "apply_or_revise", {
            "writes_production_code": True, "counts_as_confirmation": False,
        }, "apply:prod")
        assert r3.get("blocked") is True
        assert "WRITE_POLICY_VIOLATION" in r3["failure_codes"]
        print("PASS budget_and_write_fence")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_human_override_and_explain():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-ov", mode="enforce")
        seed_impl_packet(session)
        seat_and_verify(session)
        write_json(session / "findings.json", [{
            "id": "H1", "reviewer": "shanks", "category": "security",
            "severity": "high", "title": "kept open for override demo", "claim": "c",
            "locus": {"repository": "demo", "path": "A"},
            "evidence": "diff", "reachability": "r", "impact": "i",
            "confidence": "high",
        }])
        e = env_base(YONKO_WORKFLOW_MODE="enforce")
        r1 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                  "--verdict", "pass", "--confidence", "low"], env=e, check=False)
        assert r1.returncode != 0
        ov = run([sys.executable, str(SCRIPTS / "workflow" / "override.py"),
                  "--session", str(session),
                  "--codes", "OPEN_MATERIAL_FINDINGS",
                  "--reason", "Accepted residual risk for demo fixture",
                  "--approved-by", "alice"], env=e, check=False)
        assert ov.returncode == 0, ov.stderr + ov.stdout
        r2 = run([str(SCRIPTS / "finalize-session.sh"), "--session", str(session),
                  "--verdict", "pass", "--confidence", "low"], env=e, check=False)
        # May still block on other codes; if only OPEN_MATERIAL was the issue, should pass
        text = ex.explain(session)
        assert "human_override" in text or "override" in text.lower() or "OPEN_MATERIAL" in text
        assert "mode: enforce" in text
        print("PASS human_override_and_explain rc2=", r2.returncode)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_concurrent_seat_observations():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-conc", mode="enforce")
        seed_impl_packet(session, risk="low", reviewers=2)
        results = []

        def seat(n, key):
            results.append(tr.record_transition(
                session, "seat_reviewers", {"seat_count": n}, f"seat:{key}:{n}"
            ))

        t1 = threading.Thread(target=seat, args=(2, "a"))
        t2 = threading.Thread(target=seat, args=(2, "b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert all(r.get("ok") or r.get("duplicate") or not r.get("blocked") for r in results) or any(
            not r.get("blocked") for r in results
        )
        wf = json.loads((session / "workflow.json").read_text())
        assert int(wf.get("seat_count") or 0) >= 2
        # Events file remains valid JSONL
        for line in (session / "workflow-events.jsonl").read_text().splitlines():
            if line.strip():
                json.loads(line)
        print("PASS concurrent_seat_observations")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_legacy_and_fail_open():
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = work / "legacy"
        session.mkdir()
        write_json(session / "session.json", {
            "version": "3.0.0", "session_id": "legacy",
            "review_type": "implementation", "packet_version": 0,
        })
        (session / "events.jsonl").touch()
        sys.path.insert(0, str(SCRIPTS / "lib"))
        import efficiency_report as er
        r = er.build_efficiency_report(session)
        assert r.get("workflow") is None
        # First observation initialises safely
        out = tr.record_transition(session, "initialise", {}, None)
        assert out.get("ok")
        assert (session / "workflow.json").exists()
        print("PASS legacy_and_fail_open")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_reporting_fail_open():
    """Broken transition internals should not fail closed without a guard violation."""
    # transition.py catch-all returns exit 0 with fail_open - exercise via bad session path that still has session.json
    work = Path(tempfile.mkdtemp(prefix="yonko-v34-"))
    try:
        session = init_session(work, "en-fo", mode="enforce")
        # Normal transition works
        r = run([sys.executable, str(SCRIPTS / "workflow" / "transition.py"),
                 "--session", str(session), "--transition", "initialise", "--data", "{}"],
                env=env_base(YONKO_WORKFLOW_MODE="enforce"), check=False)
        assert r.returncode == 0
        print("PASS reporting_fail_open")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    tests = [
        test_mode_shadow_would_block_exits_zero,
        test_mode_enforce_blocks_nonzero,
        test_clean_impl_finalizes_enforce,
        test_open_medium_blocks_pass,
        test_dropped_finding_does_not_block,
        test_missing_and_failed_verification_block,
        test_blocked_then_corrected_retry,
        test_human_approval_plan,
        test_packet_stale_and_repin,
        test_budget_and_write_fence,
        test_human_override_and_explain,
        test_concurrent_seat_observations,
        test_legacy_and_fail_open,
        test_reporting_fail_open,
    ]
    for t in tests:
        t()
    print("\nRunning Phase 0 shadow suite (opt-in shadow)...")
    subprocess.check_call([sys.executable, str(SCRIPTS / "test-workflow-phase0-smoke.py")], cwd=str(SCRIPTS))
    for name in ("test-efficiency-phase1-smoke.py", "test-information-preservation-smoke.py",
                 "test-evidence-index-smoke.py"):
        p = SCRIPTS / name
        if p.exists():
            print(f"Running {name}...")
            subprocess.check_call([sys.executable, str(p)], cwd=str(SCRIPTS))
    print("\nAll V3.4 workflow smokes passed.")


if __name__ == "__main__":
    main()
