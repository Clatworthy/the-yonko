#!/usr/bin/env python3
"""Smoke tests for Engineering Evidence Index (stdlib). Exit 0 on success."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CLI = Path(__file__).resolve().parent / "evidence-index.py"


def run(args, env, check=True):
    r = subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if check and r.returncode != 0:
        sys.stderr.write(r.stdout + "\n" + r.stderr + "\n")
        raise SystemExit(f"FAIL {args[0]} rc={r.returncode}")
    return r


def write_session(base: Path, name: str, review_type: str, **kw) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "evidence").mkdir()
    session = {
        "session_id": name,
        "review_type": review_type,
        "status": "finalized",
        "started_at": "2026-07-26T10:00:00Z",
        "finalized_at": "2026-07-26T12:00:00Z",
        "packet_hash": "abc",
        "packet_version": 1,
    }
    session.update({k: v for k, v in kw.items() if k in ("artifact_type", "document_mode")})
    (d / "session.json").write_text(json.dumps(session, indent=2) + "\n")
    (d / "SUMMARY.md").write_text("# Summary\n")
    (d / "confidence.json").write_text(
        json.dumps(
            {
                "level": "high",
                "source": "mechanical",
                "mechanical": {},
                "chair_reasons": [],
            }
        )
        + "\n"
    )
    (d / "findings.json").write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "F1",
                        "reviewer": "shanks",
                        "category": "data-integrity",
                        "severity": "high",
                        "title": "rehome before guarded deletePlaceholder",
                        "claim": "TOCTOU on sibling shared parent",
                        "status": "accepted",
                    }
                ]
            }
        )
        + "\n"
    )
    (d / "verification.json").write_text(
        json.dumps({"verifications": [{"finding_ids": ["F1"], "verdict": "confirmed"}]})
        + "\n"
    )
    (d / "events.jsonl").write_text(json.dumps({"type": "verification_completed"}) + "\n")
    return d


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="yonko-evidence-smoke-"))
    repo = work / "engineering-evidence-index"
    sess = work / "sessions"
    sess.mkdir()
    env = os.environ.copy()
    env["YONKO_EVIDENCE_REPO"] = str(repo)

    run(["init-repo", "--path", str(repo), "--git-init"], env)
    assert (repo / "taxonomy/v1/concepts.json").exists() or (
        repo / "taxonomy/v1/concepts.yaml"
    ).exists()

    plan = write_session(sess, "sess-plan-001", "plan")
    (plan / "PLAN.approved.md").write_text(
        "# Plan TKT-3001\nKafka Flyway credit note rehome.\nAPI /v1/credit-notes\nAuth0 JWT.\n"
    )
    (plan / "evidence/plan.md").write_text("TKT-3001 Auth0 Kafka\n")
    (plan / "evidence/plan-refs.json").write_text(
        json.dumps(
            {
                "repositories_named": [{"label": "services/example-service"}],
                "repositories_inspected": [{"label": "services/example-service"}],
            }
        )
        + "\n"
    )
    (plan / "evidence/scope-risk.json").write_text(
        json.dumps({"risk": "high", "reasons": ["auth"]}) + "\n"
    )

    r = run(
        [
            "candidate",
            "--session",
            str(plan),
            "--owner",
            "ben",
            "--final-status",
            "approved",
            "--ticket",
            "TKT-3001",
            "--concept",
            "rollback",
        ],
        env,
    )
    h1 = json.loads(r.stdout)["candidate_hash"]
    r = run(
        [
            "candidate",
            "--session",
            str(plan),
            "--owner",
            "ben",
            "--final-status",
            "approved",
            "--ticket",
            "TKT-3001",
            "--concept",
            "rollback",
        ],
        env,
    )
    assert json.loads(r.stdout)["candidate_hash"] == h1, "candidate determinism failed"
    run(["validate", "--path", str(plan / "evidence-candidate")], env)
    run(
        [
            "publish-local",
            "--session",
            str(plan),
            "--candidate-hash",
            h1,
            "--approved-by",
            "ben",
        ],
        env,
    )

    impl = write_session(sess, "sess-impl-001", "implementation")
    (impl / "final.patch").write_text(
        "diff --git a/src/Foo.java b/src/Foo.java\n+++ b/src/Foo.java\n+/v1/credit-notes Kafka Auth0\n"
    )
    (impl / "evidence/repos.json").write_text(
        json.dumps(
            {"repos": [{"label": "services/example-service", "patch": "p.patch"}]}
        )
        + "\n"
    )
    (impl / "evidence/p.patch").write_text("x\n")
    (impl / "evidence/risk.json").write_text(
        json.dumps({"risk": "high", "reasons": []}) + "\n"
    )
    r = run(
        [
            "candidate",
            "--session",
            str(impl),
            "--owner",
            "ben",
            "--final-status",
            "pass",
            "--ticket",
            "TKT-3001",
            "--informed-by",
            "sess-plan-001__plan",
        ],
        env,
    )
    run(
        [
            "publish-local",
            "--session",
            str(impl),
            "--candidate-hash",
            json.loads(r.stdout)["candidate_hash"],
            "--approved-by",
            "ben",
        ],
        env,
    )

    pap = write_session(
        sess, "sess-pap-001", "document", artifact_type="pap", document_mode="create"
    )
    (pap / "PAP.final.md").write_text("# PAP TKT-3002 customer features billing Auth0\n")
    (pap / "evidence/doc-refs.json").write_text(
        json.dumps({"repositories_named": [{"label": "frontend/app"}]})
        + "\n"
    )
    (pap / "evidence/scope-risk.json").write_text(
        json.dumps({"risk": "medium", "reasons": []}) + "\n"
    )
    r = run(
        [
            "candidate",
            "--session",
            str(pap),
            "--owner",
            "ben",
            "--final-status",
            "approved",
            "--ticket",
            "TKT-3002",
            "--concept",
            "customer-features",
        ],
        env,
    )
    run(
        [
            "publish-local",
            "--session",
            str(pap),
            "--candidate-hash",
            json.loads(r.stdout)["candidate_hash"],
            "--approved-by",
            "ben",
        ],
        env,
    )

    adr = write_session(
        sess, "sess-adr-001", "document", artifact_type="adr", document_mode="review"
    )
    (adr / "ADR.final.md").write_text("# ADR rejected Redis caching\n")
    (adr / "evidence/doc-refs.json").write_text(
        json.dumps({"repositories_named": [{"label": "services/gateway"}]}) + "\n"
    )
    (adr / "evidence/scope-risk.json").write_text(
        json.dumps({"risk": "low", "reasons": []}) + "\n"
    )
    r = run(
        [
            "candidate",
            "--session",
            str(adr),
            "--owner",
            "ben",
            "--final-status",
            "rejected",
            "--ticket",
            "TKT-3003",
        ],
        env,
    )
    run(
        [
            "publish-local",
            "--session",
            str(adr),
            "--candidate-hash",
            json.loads(r.stdout)["candidate_hash"],
            "--approved-by",
            "ben",
        ],
        env,
    )

    rb = write_session(sess, "sess-impl-rb", "implementation")
    (rb / "final.patch").write_text("diff --git a/x b/x\n+rollback Kafka\n")
    (rb / "evidence/repos.json").write_text(
        json.dumps({"repos": [{"label": "services/sdi", "patch": "p.patch"}]}) + "\n"
    )
    (rb / "evidence/p.patch").write_text("x\n")
    (rb / "evidence/risk.json").write_text(
        json.dumps({"risk": "medium", "reasons": []}) + "\n"
    )
    r = run(
        [
            "candidate",
            "--session",
            str(rb),
            "--owner",
            "ben",
            "--final-status",
            "rolled_back",
            "--ticket",
            "TKT-3004",
        ],
        env,
    )
    eid = json.loads(r.stdout)["evidence_id"]
    run(
        [
            "publish-local",
            "--session",
            str(rb),
            "--candidate-hash",
            json.loads(r.stdout)["candidate_hash"],
            "--approved-by",
            "ben",
        ],
        env,
    )
    run(
        [
            "append-event",
            "--evidence-id",
            eid,
            "--type",
            "rollback_performed",
            "--actor",
            "ben",
            "--payload-json",
            '{"reason":"latency"}',
        ],
        env,
    )
    run(
        [
            "append-event",
            "--evidence-id",
            eid,
            "--type",
            "record_superseded",
            "--actor",
            "ben",
            "--payload-json",
            "{}",
        ],
        env,
    )

    run(["rebuild", "--path", str(repo)], env)
    a = (repo / "indexes/v1/by-ticket.json").read_text()
    run(["rebuild", "--path", str(repo)], env)
    assert a == (repo / "indexes/v1/by-ticket.json").read_text(), "index determinism failed"

    r = run(["query", "--from-repo", "--ticket", "TKT-3001"], env)
    assert json.loads(r.stdout)["count"] >= 1

    r = run(
        [
            "query",
            "--from-repo",
            "--similar",
            "--api",
            "/v1/credit-notes",
            "--like-concept",
            "auth",
            "--like-repository",
            "services/example-service",
            "--artifact-type",
            "plan",
        ],
        env,
    )
    body = json.loads(r.stdout)
    assert body["count"] >= 1, f"similar expected hits, got {body}"

    r = run(
        [
            "query",
            "--from-repo",
            "--repeated-mistakes",
            "--finding-pattern",
            "guarded-delete-before-eligibility",
        ],
        env,
    )
    mist = json.loads(r.stdout)["results"]
    assert mist and mist[0]["count"] >= 2

    bad = write_session(sess, "sess-secret", "plan")
    (bad / "PLAN.approved.md").write_text("AUTH0_CLIENT_SECRET=supersecretvalue\n")
    (bad / "evidence/plan.md").write_text("x\n")
    (bad / "evidence/plan-refs.json").write_text("{}\n")
    (bad / "evidence/scope-risk.json").write_text(json.dumps({"risk": "low"}) + "\n")
    r = run(
        [
            "candidate",
            "--session",
            str(bad),
            "--owner",
            "ben",
            "--final-status",
            "approved",
            "--ticket",
            "TKT-9999",
        ],
        env,
        check=False,
    )
    assert r.returncode != 0, "secret scan should fail"

    events = next((repo / "records").glob(f"*/{eid}/events.jsonl"))
    lines = [ln for ln in events.read_text().splitlines() if ln.strip()]
    tampered = json.loads(lines[-1])
    tampered["previous_event_hash"] = "deadbeef"
    lines[-1] = json.dumps(tampered, sort_keys=True)
    events.write_text("\n".join(lines) + "\n")
    r = run(["validate", "--path", str(events.parent)], env, check=False)
    assert r.returncode != 0, "tamper should fail validate"

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True
    )
    assert log.returncode != 0 or not log.stdout.strip(), "unexpected git commits"

    # Disabled-adapter compatibility: unset repo -> publish-local refuses
    env2 = os.environ.copy()
    env2.pop("YONKO_EVIDENCE_REPO", None)
    # Isolate from any pre-existing user cache
    env2["HOME"] = str(work / "empty-home")
    (work / "empty-home").mkdir(exist_ok=True)
    r = run(
        [
            "publish-local",
            "--session",
            str(plan),
            "--candidate-hash",
            h1,
            "--approved-by",
            "ben",
        ],
        env2,
        check=False,
    )
    assert r.returncode != 0
    assert "not configured" in (r.stderr + r.stdout).lower() or "missing" in (r.stderr + r.stdout).lower() or "ERROR" in (r.stderr + r.stdout)

    print("ALL SMOKE TESTS PASSED")
    print("work=", work)


if __name__ == "__main__":
    main()
