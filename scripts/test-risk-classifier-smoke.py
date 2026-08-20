#!/usr/bin/env python3
"""Regression smoke: risk classifiers must not over-fire on help/docs prose."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _seed_workflow(session: Path, review_type: str) -> None:
    (session / "workflow.json").write_text(
        json.dumps(
            {
                "workflow_version": "1.0.0",
                "mode": "enforce",
                "review_type": review_type,
                "artifact_type": None,
                "current_state": "EVIDENCE_READY",
                "packet_hash": None,
                "packet_stale": False,
                "confirmation_rounds": 0,
                "review_rounds": 0,
                "rematch_count": 0,
                "seat_count": 0,
                "would_block_count": 0,
                "blocked_count": 0,
                "override_count": 0,
                "last_transition": "collect_evidence",
                "last_failure_codes": [],
                "active_overrides": [],
                "seen_idempotency_keys": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (session / "workflow-events.jsonl").write_text("", encoding="utf-8")


def _write_impl_session(tmp: Path, patch: str) -> Path:
    session = tmp / "impl"
    evid = session / "evidence"
    evid.mkdir(parents=True)
    (evid / "repos.json").write_text(
        json.dumps({"repos": [{"name": "demo", "patch": "demo.patch"}]}) + "\n",
        encoding="utf-8",
    )
    (evid / "demo.patch").write_text(patch, encoding="utf-8")
    (session / "session.json").write_text(
        json.dumps({"review_type": "implementation", "status": "evidence_collected"}) + "\n",
        encoding="utf-8",
    )
    (session / "events.jsonl").write_text("", encoding="utf-8")
    _seed_workflow(session, "implementation")
    return session


def _write_plan_session(tmp: Path, plan: str) -> Path:
    session = tmp / "plan"
    evid = session / "evidence"
    evid.mkdir(parents=True)
    (evid / "plan.md").write_text(plan, encoding="utf-8")
    (evid / "plan-refs.json").write_text(
        json.dumps(
            {
                "review_type": "plan",
                "repositories_named": ["example-docs"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (session / "session.json").write_text(
        json.dumps({"review_type": "plan", "status": "evidence_collected"}) + "\n",
        encoding="utf-8",
    )
    (session / "events.jsonl").write_text("", encoding="utf-8")
    _seed_workflow(session, "plan")
    return session


class RiskClassifierSmoke(unittest.TestCase):
    def test_help_markdown_mentions_do_not_raise_critical(self) -> None:
        patch = """diff --git a/src/content/docs/api.md b/src/content/docs/api.md
--- a/src/content/docs/api.md
+++ b/src/content/docs/api.md
@@ -1,3 +1,6 @@
+# API help
+Authentication details are in the PDF.
+Billing needs to be separate per entity.
+Discuss webhook configuration with support.
+Swagger interface for interactive testing.
"""
        with tempfile.TemporaryDirectory() as td:
            session = _write_impl_session(Path(td), patch)
            r = _run([str(SCRIPTS / "classify-risk.sh"), "--session", str(session)])
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            risk = json.loads((session / "evidence" / "risk.json").read_text(encoding="utf-8"))
            self.assertNotEqual(risk["risk"], "critical", risk)
            crit = [x for x in risk["reason_details"] if x["band"] == "critical"]
            self.assertEqual(crit, [], crit)

    def test_auth_path_and_invoice_code_still_critical(self) -> None:
        patch = """diff --git a/src/features/auth/user/utils.ts b/src/features/auth/user/utils.ts
--- a/src/features/auth/user/utils.ts
+++ b/src/features/auth/user/utils.ts
@@ -1,3 +1,6 @@
+import { createInvoice } from '../billing/InvoiceService';
+export function issueInvoice() {
+  return createInvoice();
+}
"""
        with tempfile.TemporaryDirectory() as td:
            session = _write_impl_session(Path(td), patch)
            r = _run([str(SCRIPTS / "classify-risk.sh"), "--session", str(session)])
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            risk = json.loads((session / "evidence" / "risk.json").read_text(encoding="utf-8"))
            self.assertEqual(risk["risk"], "critical", risk)
            reasons = set(risk["reasons"])
            self.assertIn("authorisation or auth middleware path changed", reasons)
            self.assertIn(
                "money / billing / invoice mutation path changed",
                reasons,
                "generic invoice/billing identifiers must still raise billing",
            )

    def test_plan_prose_topic_list_not_critical(self) -> None:
        plan = """
# Help automation plan
High risk topics (pricing, privacy, security, tenancy, API auth): must open MR.
Charge / ownership stance: engineering owns the site.
Use demo tenant screenshots only.
CDN purge after deploy. Content publish via MR.
Rollback: revert commit.
"""
        with tempfile.TemporaryDirectory() as td:
            session = _write_plan_session(Path(td), plan)
            r = _run([str(SCRIPTS / "classify-scope-risk.sh"), "--session", str(session)])
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            risk = json.loads((session / "evidence" / "scope-risk.json").read_text(encoding="utf-8"))
            self.assertNotEqual(risk["risk"], "critical", risk)
            crit = [x for x in risk["reason_details"] if x["band"] == "critical"]
            self.assertEqual(crit, [], crit)

    def test_login_howto_naming_auth0_not_critical(self) -> None:
        plan = """
# DOC-1001 login article trial
Verify UI labels on live Auth0 Universal Login.
Steps match live Auth0: Continue, Don't remember your password?
Do not document Auth0 client IDs in help articles.
H1: How do I log in to the product?
"""
        with tempfile.TemporaryDirectory() as td:
            session = _write_plan_session(Path(td), plan)
            r = _run([str(SCRIPTS / "classify-scope-risk.sh"), "--session", str(session)])
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            risk = json.loads((session / "evidence" / "scope-risk.json").read_text(encoding="utf-8"))
            self.assertNotEqual(risk["risk"], "critical", risk)
            crit = [x["reason"] for x in risk["reason_details"] if x["band"] == "critical"]
            self.assertEqual(crit, [], crit)

    def test_auth_config_change_still_critical(self) -> None:
        plan = """
# Auth0 actions migration
Implement Auth0 actions for login hooks and change authentication middleware.
Update RBAC permission checks and JWT validation.
"""
        with tempfile.TemporaryDirectory() as td:
            session = _write_plan_session(Path(td), plan)
            r = _run([str(SCRIPTS / "classify-scope-risk.sh"), "--session", str(session)])
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            risk = json.loads((session / "evidence" / "scope-risk.json").read_text(encoding="utf-8"))
            self.assertEqual(risk["risk"], "critical", risk)
            reasons = set(risk["reasons"])
            self.assertIn("stated scope touches authorisation or authentication", reasons)


if __name__ == "__main__":
    unittest.main()
