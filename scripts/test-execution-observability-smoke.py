#!/usr/bin/env python3
"""Smoke: finalize writes observational evidence/execution.json (budget vs calls)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def _run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


class ExecutionObservabilitySmoke(unittest.TestCase):
    def test_finalize_writes_execution_json_separating_budget_and_calls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "sess"
            evid = session / "evidence"
            evid.mkdir(parents=True)
            (session / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": "exec-obs-1",
                        "started_at": "20260730T100000Z",
                        "review_type": "implementation",
                        "mode": "standard",
                        "force_route": None,
                        "risk": "medium",
                        "status": "packet_ready",
                        "subagent_calls": 0,
                        "round": 1,
                        "packet_hash": "abc",
                        "packet_version": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (evid / "repos.json").write_text(json.dumps({"repos": []}) + "\n", encoding="utf-8")
            (evid / "risk.json").write_text(
                json.dumps(
                    {
                        "risk": "medium",
                        "reviewers": 3,
                        "maximum_subagent_calls": 5,
                        "force": None,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (evid / "routing.json").write_text(
                json.dumps({"seats": ["shanks", "blackbeard", "buggy"], "risk_band": "medium"})
                + "\n",
                encoding="utf-8",
            )
            (session / "packet.md").write_text("# packet\n", encoding="utf-8")
            (session / "events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-07-30T10:01:00Z",
                                "type": "reviewers_seated",
                                "data": {"count": 3},
                            }
                        ),
                        json.dumps(
                            {
                                "ts": "2026-07-30T10:02:00Z",
                                "type": "task_call",
                                "data": {
                                    "seat": "shanks",
                                    "model": "gpt-5.6-terra-medium",
                                    "count": 1,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "ts": "2026-07-30T10:03:00Z",
                                "type": "task_call",
                                "data": {
                                    "seat": "blackbeard",
                                    "model": "claude-opus-5-thinking-high",
                                    "count": 1,
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (session / "workflow.json").write_text(
                json.dumps(
                    {
                        "workflow_version": "1.0.0",
                        "mode": "shadow",
                        "review_type": "implementation",
                        "current_state": "SCOPED_OK",
                        "packet_hash": "abc",
                        "seat_count": 3,
                        "seen_idempotency_keys": [],
                        "active_overrides": [],
                        "last_failure_codes": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (session / "workflow-events.jsonl").write_text("", encoding="utf-8")

            env = dict(**{k: v for k, v in __import__("os").environ.items()})
            env["YONKO_WORKFLOW_MODE"] = "shadow"
            r = _run(
                [
                    str(SCRIPTS / "finalize-session.sh"),
                    "--session",
                    str(session),
                    "--verdict",
                    "pass",
                    "--confidence",
                    "medium",
                ],
                env=env,
            )
            # observe mode may still finalize; if blocked, assert on file if written
            exec_path = evid / "execution.json"
            if not exec_path.exists():
                self.fail(f"execution.json missing; rc={r.returncode} err={r.stderr[-800:]}")
            ex = json.loads(exec_path.read_text(encoding="utf-8"))
            self.assertEqual(ex["seatBudget"], 3)
            self.assertEqual(ex["subagentCalls"], 2, "reviewers_seated must not inflate actual calls")
            self.assertEqual(
                ex["models"],
                ["gpt-5.6-terra-medium", "claude-opus-5-thinking-high"],
            )
            self.assertTrue(ex["completed"])
            self.assertEqual(ex["band"], "medium")
            self.assertEqual(ex["policy"], "observational_only_never_influences_routing")


if __name__ == "__main__":
    unittest.main()
