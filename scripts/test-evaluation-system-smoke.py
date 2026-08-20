#!/usr/bin/env python3
"""Adversarial evaluation system smokes (Yonko 3.9.0)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.evaluation.capture import (  # noqa: E402
    build_eval_candidate,
    build_measurement,
    capture_or_fail_open,
    capture_session_observability,
)
from lib.evaluation.path_quality import assess_path_quality  # noqa: E402
from lib.evaluation.taxonomy import map_dropped_disposition  # noqa: E402
from lib.review_quality_ledger import build_row  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(str(data), encoding="utf-8")


class EvaluationAdversarySmoke(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yonko-eval-"))
        self.sessions = self.tmp / "sessions"
        self.sessions.mkdir()
        os.environ["YONKO_SESSIONS_ROOT"] = str(self.sessions)
        os.environ["YONKO_SKILL_ROOT"] = str(SKILL)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _base_session(self, name: str, review_type: str = "implementation") -> Path:
        s = self.sessions / name
        s.mkdir()
        _write(
            s / "session.json",
            {"session_id": name, "review_type": review_type, "packet_hash": "abc123"},
        )
        _write(s / "metrics.json", {"review_type": review_type, "packet_hash": "abc123", "verdict": "remand"})
        _write(s / "outcome.json", {"review_outcome": "remand", "schema_version": 1})
        _write(s / "packet.meta.json", {"packet_hash": "abc123deadbeef" * 2})
        (s / "evidence").mkdir()
        _write(s / "evidence" / "risk.json", {"risk": "high"})
        _write(s / "evidence" / "routing.json", {"seats": ["shanks", "blackbeard", "buggy"]})
        return s

    def test_taxonomy_ta3227_shaped_dropped_empty_reason_unknown(self) -> None:
        f = {"id": "Y4", "action": "drop", "reason": "", "title": "noise"}
        self.assertEqual(map_dropped_disposition(f), "unknown_not_adjudicated")
        f2 = {"id": "Y3", "action": "drop", "reason": "Ungrounded; tree clean."}
        self.assertEqual(map_dropped_disposition(f2), "rejected_unsupported")
        f3 = {"id": "Y1", "action": "applied", "reason": "Applied and pushed"}
        self.assertEqual(map_dropped_disposition(f3), "unknown_not_adjudicated")

    def test_plan_array_form_unknown_dispositions(self) -> None:
        s = self._base_session("plan-only", review_type="plan")
        _write(
            s / "findings.json",
            {
                "plan_findings": [
                    {
                        "id": "P1",
                        "title": "scope gap",
                        "evidence_kind": "code_inspected",
                        "evidence_reference": "a/b.py",
                        "production_consequence": "bad",
                    }
                ]
            },
        )
        m = build_measurement(s)
        self.assertEqual(m["adjudication_state"], "plan_array_form")
        counts = m["dispositions"]["counts"]
        self.assertEqual(counts.get("unknown_not_adjudicated"), 1)
        self.assertNotIn("rejected_false", counts)

    def test_empty_findings_path_quality_not_applicable(self) -> None:
        pq = assess_path_quality(
            review_type="implementation", findings=[], seats_completed=True
        )
        self.assertEqual(pq["status"], "not_applicable")
        self.assertFalse(pq.get("vacuous_pass"))
        s = self._base_session("empty")
        _write(s / "findings.json", {"accepted": [], "dropped": [], "held": [], "notes": []})
        # seats completed
        for seat in ("shanks", "blackbeard"):
            rt = s / "runtime" / seat
            rt.mkdir(parents=True)
            _write(rt / "result.json", {"seat": seat, "completed": True, "duration_ms": 10})
            _write(rt / "findings.json", {"disposition": "Content", "findings": []})
        m = build_measurement(s)
        self.assertEqual(m["adjudication_state"], "empty_findings")
        self.assertIn("empty_findings", m["flags"])
        self.assertEqual(m["path_quality"]["status"], "not_applicable")
        cand = build_eval_candidate(m)
        self.assertIn("weak_or_empty", cand["reasons"])
        self.assertEqual(cand["strong_reasons"], [])

    def test_held_disposition(self) -> None:
        s = self._base_session("held")
        _write(
            s / "findings.json",
            {
                "accepted": [],
                "dropped": [],
                "held": [{"id": "H1", "title": "maybe", "reason": "inconclusive"}],
            },
        )
        m = build_measurement(s)
        self.assertEqual(m["dispositions"]["counts"].get("chair_inconclusive"), 1)

    def test_missing_runtime_not_run(self) -> None:
        s = self._base_session("no-rt")
        _write(
            s / "findings.json",
            {"accepted": [{"id": "A1", "title": "x", "reviewer": "shanks", "category": "c", "locus": {"path": "a"}}], "dropped": [], "held": []},
        )
        m = build_measurement(s)
        self.assertIn("runtime_missing", m["flags"])
        statuses = {x["seat"]: x["status"] for x in m["seats"]}
        self.assertEqual(statuses.get("shanks"), "not_run")
        self.assertIsNone(next(x for x in m["seats"] if x["seat"] == "shanks")["raw_findings"])

    def test_capture_then_ledger_projection_no_circular(self) -> None:
        s = self._base_session("proj")
        _write(
            s / "findings.json",
            {
                "accepted": [
                    {
                        "id": "A1",
                        "reviewer": "shanks",
                        "category": "correctness",
                        "severity": "high",
                        "title": "Bug",
                        "locus": {"path": "A.java"},
                        "evidence": "diff",
                        "reachability": "yes",
                        "impact": "money",
                    }
                ],
                "dropped": [
                    {"id": "D1", "action": "drop", "reason": "", "title": "noise", "reviewer": "buggy"}
                ],
                "held": [],
            },
        )
        rt = s / "runtime" / "shanks"
        rt.mkdir(parents=True)
        _write(rt / "result.json", {"seat": "shanks", "completed": True, "duration_ms": 100, "usage": {"cost": 0.01}})
        _write(rt / "findings.json", {"disposition": "Remand", "findings": [{"id": "A1", "title": "Bug", "category": "correctness", "locus": {"path": "A.java"}, "reviewer": "shanks"}]})

        cap = capture_session_observability(s, write=True, upsert_index=True, sessions_root_override=self.sessions)
        self.assertTrue((s / "evaluation" / "review-measurement.json").is_file())
        self.assertTrue((s / "evaluation" / "council-effectiveness.json").is_file())
        row = build_row(s)
        self.assertTrue(row.get("evaluation_projection"))
        self.assertEqual(row["session_id"], "proj")
        # dropped empty reason must not invent rejected_false in measurement
        m = cap["measurement"]
        self.assertEqual(m["dispositions"]["counts"].get("unknown_not_adjudicated"), 1)

    def test_fail_open_writes_error(self) -> None:
        s = self._base_session("boom")
        with mock.patch(
            "lib.evaluation.capture.build_measurement",
            side_effect=RuntimeError("boom"),
        ):
            with mock.patch(
                "lib.evaluation.capture.load_observability_evaluation",
                return_value={"capture_on_finalize": True, "fail_open": True},
            ):
                out = capture_or_fail_open(s, sessions_root_override=self.sessions)
        self.assertFalse(out.get("ok"))
        self.assertTrue(out.get("fail_open"))
        self.assertTrue((s / "evaluation" / "capture.error.txt").is_file())

    def test_path_quality_review_type_specific(self) -> None:
        plan_f = {
            "id": "P1",
            "evidence_kind": "assumption",
            "evidence_reference": "doc",
            "production_consequence": "risk",
        }
        impl_f = {"id": "I1", "title": "x"}  # missing locus/evidence
        pq_plan = assess_path_quality(review_type="plan", findings=[plan_f], seats_completed=True)
        self.assertEqual(pq_plan["status"], "pass")
        pq_impl = assess_path_quality(
            review_type="implementation", findings=[impl_f], seats_completed=True
        )
        self.assertEqual(pq_impl["status"], "fail")

    def test_promote_refuse_matrix(self) -> None:
        s = self._base_session("promo")
        ph = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
        _write(s / "packet.meta.json", {"packet_hash": ph})
        _write(
            s / "findings.json",
            {
                "accepted": [
                    {
                        "id": "A1",
                        "reviewer": "shanks",
                        "title": "t",
                        "category": "c",
                        "locus": {"path": "a"},
                        "evidence": "e",
                        "reachability": "yes",
                        "impact": "i",
                    }
                ],
                "dropped": [],
                "held": [],
            },
        )
        capture_session_observability(s, write=True, upsert_index=False)
        script = SKILL / "scripts" / "evals" / "promote-case.sh"
        # missing hash
        r = subprocess.run(
            ["bash", str(script), "--session", str(s), "--approved-by", "ben"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 2)
        # mismatch
        r2 = subprocess.run(
            [
                "bash",
                str(script),
                "--session",
                str(s),
                "--approved-by",
                "ben",
                "--confirm-hash",
                "deadbeef",
                "--case-id",
                f"case-promo-{self.tmp.name}",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r2.returncode, 3)
        # success
        case_id = f"case-ok-{Path(self.tmp).name}"
        r3 = subprocess.run(
            [
                "bash",
                str(script),
                "--session",
                str(s),
                "--approved-by",
                "ben",
                "--confirm-hash",
                ph,
                "--case-id",
                case_id,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        case_path = SKILL / "evals" / "cases" / f"{case_id}.json"
        self.assertTrue(case_path.is_file())
        # no overwrite
        r4 = subprocess.run(
            [
                "bash",
                str(script),
                "--session",
                str(s),
                "--approved-by",
                "ben",
                "--confirm-hash",
                ph,
                "--case-id",
                case_id,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r4.returncode, 5)
        case_path.unlink(missing_ok=True)
        # cleanup manifest line is ok to leave

    def test_replay_profile_fingerprint_and_cross_mode(self) -> None:
        from lib.evaluation.config import skill_root

        profile = skill_root() / "config" / "execution-profile.json"
        before = profile.read_bytes() if profile.is_file() else b""
        s = self._base_session("replay-src")
        ph = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
        _write(s / "packet.meta.json", {"packet_hash": ph})
        _write(s / "packet.md", "=== PACKET ===\nhello\n")
        _write(s / "findings.json", {"accepted": [], "dropped": [], "held": []})
        capture_session_observability(s, write=True, upsert_index=False)

        case_id = f"replay-{Path(self.tmp).name}"
        _write(
            SKILL / "evals" / "cases" / f"{case_id}.json",
            {
                "schema_version": 1,
                "case_id": case_id,
                "source_session_id": s.name,
                "source_session_path": str(s),
                "packet_hash": ph,
                "creation_reason": "test",
                "approved_by": "test",
            },
        )
        run_a = f"{case_id}-frozen"
        r = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "evals" / "replay-case.py"),
                "--case-id",
                case_id,
                "--mode",
                "frozen_packet",
                "--run-id",
                run_a,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = profile.read_bytes() if profile.is_file() else b""
        self.assertEqual(before, after)

        run_b = f"{case_id}-full"
        r2 = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "evals" / "replay-case.py"),
                "--case-id",
                case_id,
                "--mode",
                "full_pipeline",
                "--run-id",
                run_b,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

        c = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "evals" / "compare-runs.py"),
                "--run-a",
                run_a,
                "--run-b",
                run_b,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(c.returncode, 3)
        self.assertIn("cross_mode_compare_forbidden", c.stdout)

        # cleanup
        shutil.rmtree(SKILL / "evals" / "results" / run_a, ignore_errors=True)
        shutil.rmtree(SKILL / "evals" / "results" / run_b, ignore_errors=True)
        (SKILL / "evals" / "cases" / f"{case_id}.json").unlink(missing_ok=True)

    def test_insufficient_sample_blocks_strong_propose(self) -> None:
        # empty sessions root → n=0 < 10
        r = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "evals" / "propose-improvement.py"),
                "--proposal-id",
                f"prop-{Path(self.tmp).name}",
                "--title",
                "test",
                "--sessions-root",
                str(self.sessions),
                "--strong-claim",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 3)
        self.assertIn("insufficient_sample", r.stdout)
        # weak propose ok
        r2 = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "evals" / "propose-improvement.py"),
                "--proposal-id",
                f"prop-weak-{Path(self.tmp).name}",
                "--title",
                "test weak",
                "--sessions-root",
                str(self.sessions),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        prop_path = SKILL / "improvements" / "candidates" / f"prop-weak-{Path(self.tmp).name}.json"
        data = json.loads(prop_path.read_text())
        self.assertTrue(data["insufficient_sample"])
        self.assertTrue(data["suggest_only"])
        self.assertEqual(data["may_edit"], [])
        prop_path.unlink(missing_ok=True)

    def test_no_yonko_dry_in_bootstrap_outputs(self) -> None:
        # Ensure skill evals trees don't invent YONKO-DRY ids by default
        for p in (SKILL / "evals").rglob("*"):
            if p.is_file() and p.suffix in (".json", ".md", ".jsonl"):
                text = p.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn("YONKO-DRY", text)

    def test_config_has_no_promote_automatically_or_ci_gate(self) -> None:
        import re

        ev = (SKILL / "config" / "evaluation.yaml").read_text()
        obs = (SKILL / "config" / "observability-policy.yaml").read_text()
        self.assertIsNone(re.search(r"(?m)^\s*promote_automatically\s*:", ev))
        self.assertIsNone(re.search(r"(?m)^\s*ci_gate\s*:", ev))
        self.assertIsNone(re.search(r"(?m)^\s*promote_automatically\s*:", obs))
        self.assertIsNone(re.search(r"(?m)^\s*ci_gate\s*:", obs))
        self.assertIn("capture_on_finalize", obs)
        self.assertIn("fail_open", obs)

    def test_aggregate_emits_insufficient_sample(self) -> None:
        r = subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "evals" / "aggregate-evaluation.py"),
                "--sessions-root",
                str(self.sessions),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["insufficient_sample"])
        self.assertFalse(data["strong_claims_allowed"])

    def _finalize_ready_session(self, name: str) -> Path:
        """Minimal session that finalize-session.sh accepts under workflow shadow."""
        s = self.sessions / name
        s.mkdir(parents=True, exist_ok=True)
        evid = s / "evidence"
        evid.mkdir(exist_ok=True)
        ph = "finalizehash00112233445566778899aabbccddeeff001122334455667788"
        _write(
            s / "session.json",
            {
                "session_id": name,
                "started_at": "20260804T090000Z",
                "review_type": "implementation",
                "mode": "standard",
                "force_route": None,
                "risk": "medium",
                "status": "packet_ready",
                "subagent_calls": 0,
                "round": 1,
                "packet_hash": ph,
                "packet_version": 1,
            },
        )
        _write(s / "packet.meta.json", {"packet_hash": ph, "packet_bytes": 12})
        _write(s / "packet.md", "# packet\n")
        _write(evid / "repos.json", {"repos": []})
        _write(
            evid / "risk.json",
            {
                "risk": "medium",
                "reviewers": 3,
                "maximum_subagent_calls": 5,
                "force": None,
            },
        )
        _write(
            evid / "routing.json",
            {"seats": ["shanks", "blackbeard", "buggy"], "risk_band": "medium"},
        )
        _write(
            s / "findings.json",
            {
                "accepted": [
                    {
                        "id": "A1",
                        "reviewer": "shanks",
                        "category": "correctness",
                        "severity": "high",
                        "title": "Finalize smoke finding",
                        "locus": {"path": "A.java"},
                        "evidence": "diff",
                        "reachability": "yes",
                        "impact": "money",
                    }
                ],
                "dropped": [],
                "held": [],
            },
        )
        rt = s / "runtime" / "shanks"
        rt.mkdir(parents=True, exist_ok=True)
        _write(
            rt / "result.json",
            {
                "seat": "shanks",
                "completed": True,
                "duration_ms": 50,
                "usage": {"cost": 0.01},
            },
        )
        _write(
            rt / "findings.json",
            {
                "disposition": "Remand",
                "findings": [
                    {
                        "id": "A1",
                        "reviewer": "shanks",
                        "category": "correctness",
                        "title": "Finalize smoke finding",
                        "locus": {"path": "A.java"},
                    }
                ],
            },
        )
        _write(
            s / "events.jsonl",
            json.dumps(
                {
                    "ts": "2026-08-04T09:01:00Z",
                    "type": "reviewers_seated",
                    "data": {"count": 3},
                }
            )
            + "\n",
        )
        _write(
            s / "workflow.json",
            {
                "workflow_version": "1.0.0",
                "mode": "shadow",
                "review_type": "implementation",
                "current_state": "SCOPED_OK",
                "packet_hash": ph,
                "seat_count": 3,
                "seen_idempotency_keys": [],
                "active_overrides": [],
                "last_failure_codes": [],
            },
        )
        _write(s / "workflow-events.jsonl", "")
        return s

    def _finalize_env(self) -> dict:
        env = dict(os.environ)
        env["YONKO_WORKFLOW_MODE"] = "shadow"
        env["YONKO_SESSIONS_ROOT"] = str(self.sessions)
        env["YONKO_SKILL_ROOT"] = str(SKILL)
        env["YONKO_SCRIPTS_DIR"] = str(SCRIPTS)
        env.pop("YONKO_EVAL_FORCE_CAPTURE_FAIL", None)
        return env

    def _run_finalize(self, session: Path, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(SCRIPTS / "finalize-session.sh"),
                "--session",
                str(session),
                "--verdict",
                "pass",
                "--confidence",
                "medium",
            ],
            capture_output=True,
            text=True,
            env=env or self._finalize_env(),
        )

    @staticmethod
    def _count_jsonl_session(path: Path, session_id: str) -> int:
        if not path.is_file():
            return 0
        n = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("session_id") == session_id:
                n += 1
        return n

    def test_finalize_artefacts_and_upsert_idempotent(self) -> None:
        """E2E: finalize creates default evaluation artefacts; re-finalize upserts once."""
        s = self._finalize_ready_session("fin-e2e-1")
        r1 = self._run_finalize(s)
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)

        for rel in (
            "metrics.json",
            "confidence.json",
            "outcome.json",
            "SUMMARY.md",
            "review-quality.json",
            "evaluation/review-measurement.json",
            "evaluation/council-effectiveness.json",
            "evaluation/council-effectiveness.md",
            "evaluation/eval-candidate.json",
        ):
            self.assertTrue((s / rel).is_file(), f"missing {rel}")

        outcome1 = (s / "outcome.json").read_text(encoding="utf-8")
        m = json.loads((s / "evaluation" / "review-measurement.json").read_text())
        self.assertEqual(m["session_id"], "fin-e2e-1")
        self.assertEqual(m["schema_version"], 1)

        idx = self.sessions / "_rollup" / "measurement-index.jsonl"
        ledger = self.sessions / "_rollup" / "review-quality-ledger.jsonl"
        self.assertEqual(self._count_jsonl_session(idx, "fin-e2e-1"), 1)
        self.assertEqual(self._count_jsonl_session(ledger, "fin-e2e-1"), 1)

        r2 = self._run_finalize(s)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(self._count_jsonl_session(idx, "fin-e2e-1"), 1)
        self.assertEqual(self._count_jsonl_session(ledger, "fin-e2e-1"), 1)
        self.assertTrue((s / "outcome.json").is_file())
        # Re-finalize may rewrite outcome; must remain valid JSON object
        outcome2 = json.loads((s / "outcome.json").read_text(encoding="utf-8"))
        self.assertIsInstance(outcome2, dict)
        self.assertIn("review_outcome", outcome2)
        # First outcome payload was authoritative at write time
        self.assertTrue(outcome1.strip())

    def test_finalize_capture_fail_open_preserves_outcome(self) -> None:
        """Capture failure on finalize writes capture.error.txt; outcome remains."""
        s = self._finalize_ready_session("fin-failopen-1")
        env = self._finalize_env()
        env["YONKO_EVAL_FORCE_CAPTURE_FAIL"] = "1"
        r = self._run_finalize(s, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        self.assertTrue((s / "metrics.json").is_file())
        self.assertTrue((s / "confidence.json").is_file())
        self.assertTrue((s / "outcome.json").is_file())
        outcome = json.loads((s / "outcome.json").read_text(encoding="utf-8"))
        self.assertIsInstance(outcome, dict)
        self.assertIn("review_outcome", outcome)

        err = s / "evaluation" / "capture.error.txt"
        self.assertTrue(err.is_file(), "expected capture.error.txt under fail-open")
        self.assertIn("YONKO_EVAL_FORCE_CAPTURE_FAIL", err.read_text(encoding="utf-8"))
        # Canonical measurement must not be claimed present after forced capture fail
        self.assertFalse((s / "evaluation" / "review-measurement.json").is_file())


if __name__ == "__main__":
    unittest.main()
