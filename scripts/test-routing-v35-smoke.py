#!/usr/bin/env python3
"""V3.5 routing smoke tests - classify-change + route-reviewers (deterministic)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import routing  # noqa: E402


def _session_with_patch(patch: str) -> Path:
    td = Path(tempfile.mkdtemp(prefix="yonko-route-"))
    evid = td / "evidence"
    evid.mkdir()
    (td / "session.json").write_text(
        json.dumps({"session_id": "t", "review_type": "implementation"}),
        encoding="utf-8",
    )
    (evid / "repos.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "label": "r1",
                        "path": "/tmp/r1",
                        "branch": "main",
                        "patch": "a.patch",
                        "secrets_excluded": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (evid / "a.patch").write_text(patch, encoding="utf-8")
    return td


def test_policy_loads_signals():
    p = routing.load_policy_pair(ROOT / "config")
    assert p["signals"]["auth"], "auth signals must parse"
    assert "shanks" in p["classes"]["auth"]["seats"]
    print("PASS policy_loads_signals")


def test_readme_docs_routing():
    p = routing.load_policy_pair(ROOT / "config")
    td = _session_with_patch(
        "diff --git a/README.md b/README.md\n+++ b/README.md\n+docs only\n"
    )
    try:
        cc = routing.classify_change(td, p)
        assert "documentation" in cc["classes"]
        rt = routing.route_reviewers(cc, {"band": "trivial"}, p, luffy_ok=False)
        assert rt["seats"] == ["blackbeard"]
        assert rt["require_verifier"] is False
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS readme_docs_routing")


def test_auth_migration_routing():
    p = routing.load_policy_pair(ROOT / "config")
    td = _session_with_patch(
        "diff --git a/db/migration/V2__x.sql b/db/migration/V2__x.sql\n"
        "+++ b/db/migration/V2__x.sql\n+ALTER\n"
        "diff --git a/src/features/auth/JwtFilter.java b/src/features/auth/JwtFilter.java\n"
        "+++ b/src/features/auth/JwtFilter.java\n+RequireUser jwt\n"
    )
    try:
        cc = routing.classify_change(td, p)
        assert set(cc["classes"]) >= {"auth", "database"}
        rt = routing.route_reviewers(cc, {"band": "critical"}, p, luffy_ok=False)
        assert "shanks" in rt["seats"] and "blackbeard" in rt["seats"] and "buggy" in rt["seats"]
        assert "luffy" not in rt["seats"]
        assert rt["luffy_omitted"] is True
        assert rt["require_verifier"] is True
        assert rt["effective_floor"] == 3
        text = routing.explain_routing(rt)
        assert "Selected reviewers" in text
        assert "Authentication" in text or "auth" in text
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS auth_migration_routing")


def test_luffy_when_available():
    p = routing.load_policy_pair(ROOT / "config")
    td = _session_with_patch(
        "diff --git a/x.java b/x.java\n+++ b/x.java\n+noop\n"
    )
    try:
        cc = routing.classify_change(td, p)
        rt = routing.route_reviewers(cc, {"band": "high"}, p, luffy_ok=True)
        assert rt["seats"] == ["shanks", "blackbeard", "buggy", "luffy"]
        assert rt["effective_floor"] == 4
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS luffy_when_available")


def test_advisory_closed_enum_only():
    p = routing.load_policy_pair(ROOT / "config")
    td = _session_with_patch(
        "diff --git a/README.md b/README.md\n+++ b/README.md\n+x\n"
    )
    try:
        cc = routing.classify_change(td, p, advisory=["performance", "security-officer", "auth"])
        assert "performance" in cc["advisory_classes"]
        assert "security-officer" in cc["dropped_advisory"]
        assert "auth" in cc["classes"]
        rt = routing.route_reviewers(cc, {"band": "trivial"}, p, luffy_ok=False)
        assert "buggy" in rt["seats"]  # from performance class
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS advisory_closed_enum_only")


def test_unknown_seat_hard_fail():
    p = routing.load_policy_pair(ROOT / "config")
    bad = dict(p)
    bad["classes"] = dict(p["classes"])
    bad["classes"]["auth"] = dict(p["classes"]["auth"])
    bad["classes"]["auth"]["seats"] = ["security"]
    try:
        routing.route_reviewers(
            {"classes": ["auth"], "advisory_classes": []},
            {"band": "low"},
            bad,
            luffy_ok=False,
        )
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "unknown seat" in str(e)
    print("PASS unknown_seat_hard_fail")


def test_scripts_end_to_end():
    import subprocess

    p = routing.load_policy_pair(ROOT / "config")
    td = _session_with_patch(
        "diff --git a/auth/Filter.java b/auth/Filter.java\n+++ b/auth/Filter.java\n+jwt\n"
    )
    try:
        # risk.json required by route script
        (td / "evidence" / "risk.json").write_text(
            json.dumps({"risk": "high", "reasons": []}), encoding="utf-8"
        )
        # events.jsonl for record-event
        (td / "events.jsonl").write_text("", encoding="utf-8")
        import os

        env = os.environ.copy()
        r1 = subprocess.run(
            [str(ROOT / "scripts" / "classify-change.sh"), "--session", str(td)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert r1.returncode == 0, r1.stderr
        assert (td / "evidence" / "change-classes.json").exists()
        r2 = subprocess.run(
            [str(ROOT / "scripts" / "route-reviewers.sh"), "--session", str(td)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert r2.returncode == 0, r2.stderr
        rt = json.loads((td / "evidence" / "routing.json").read_text())
        assert "shanks" in rt["seats"]
        assert "Selected reviewers" in r2.stderr
        # byte-stable re-run
        r3 = subprocess.run(
            [str(ROOT / "scripts" / "route-reviewers.sh"), "--session", str(td)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert r3.returncode == 0
        rt2 = json.loads((td / "evidence" / "routing.json").read_text())
        assert rt == rt2
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS scripts_end_to_end")


def test_routing_seat_coverage_guard():
    """Seating must cover routing.json seats or REVIEWER_INCOMPLETE fires."""
    sys.path.insert(0, str(ROOT / "scripts" / "workflow"))
    import guards  # noqa: E402

    td = _session_with_patch(
        "diff --git a/auth/x.java b/auth/x.java\n+++ b/auth/x.java\n+jwt\n"
    )
    try:
        p = routing.load_policy_pair(ROOT / "config")
        cc = routing.classify_change(td, p)
        rt = routing.route_reviewers(cc, {"risk": "high"}, p, luffy_ok=False)
        (td / "evidence" / "routing.json").write_text(
            json.dumps(rt, indent=2), encoding="utf-8"
        )
        assert not guards._routing_seats_covered(td, {"seats": ["blackbeard"]})
        assert guards._routing_seats_covered(td, {"seats": list(rt["seats"])})
        assert guards._min_seats(td, {"review_type": "implementation"}) == rt["effective_floor"]
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS routing_seat_coverage_guard")


def main() -> int:
    test_policy_loads_signals()
    test_readme_docs_routing()
    test_auth_migration_routing()
    test_luffy_when_available()
    test_advisory_closed_enum_only()
    test_unknown_seat_hard_fail()
    test_scripts_end_to_end()
    test_routing_seat_coverage_guard()
    print("ALL V3.5 routing smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
