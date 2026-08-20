#!/usr/bin/env python3
"""Hybrid end-to-end fixture: cursor-opencode-go + shared Packet + OpenCode stub seats.

Confirms profile freeze, identical packet hash for Cursor/OpenCode seats,
findings validation path, and failure categories. No live OpenCode / paid calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.runtime import resolve_profile as rp  # noqa: E402
from lib.runtime.invoke_seat import invoke_seat  # noqa: E402
from lib.runtime.opencode_adapter import invoke_opencode_seat  # noqa: E402


class HybridOpenCodeGoFixture(unittest.TestCase):
    def _boot_session(self, repo: Path) -> Path:
        env = os.environ.copy()
        root = Path(tempfile.mkdtemp())
        env["YONKO_SESSIONS_ROOT"] = str(root)
        proc = subprocess.run(
            [str(SCRIPTS / "init-session.sh"), "--id", "hybrid-e2e-1", "--type", "implementation"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        session = Path(proc.stdout.strip())
        # Force freeze to cursor-opencode-go with configured model ids (no live list)
        profile = rp.load_profile("cursor-opencode-go")
        profile = json.loads(json.dumps(profile))
        # Keep panel defaults; optional override only for offline stub ids if needed
        freeze = rp.freeze_profile_into_session(session, profile, force=True)
        self.assertEqual(freeze["executionProfile"], "cursor-opencode-go")
        self.assertEqual(freeze.get("model_selection_version"), "2026-08-04")
        by_seat = {row["seat"]: row for row in freeze["seats"]}
        self.assertEqual(by_seat["chair"]["configured_model"], "auto")
        self.assertEqual(by_seat["chair"]["resolved_model"], "auto")
        self.assertEqual(by_seat["shanks"]["resolved_model"], "grok")
        self.assertEqual(by_seat["luffy"]["runtime"], "opencode")
        self.assertEqual(by_seat["luffy"]["model"], "opencode-go/qwen3.7-plus")
        self.assertEqual(by_seat["blackbeard"]["model"], "opencode-go/deepseek-v4-flash")
        self.assertEqual(by_seat["buggy"]["model"], "opencode-go/gpt-5.6-luna")
        self.assertEqual(by_seat["luffy"]["activation"], "escalation_only")

        packet_hash = "pkt-hybrid-fixed-hash"
        (session / "packet.md").write_text(
            "=== YONKO DOCKET ===\nHybrid fixture.\n\n=== DIFF: demo ===\n+ok\n",
            encoding="utf-8",
        )
        (session / "packet.meta.json").write_text(
            json.dumps({"packet_hash": packet_hash}) + "\n", encoding="utf-8"
        )
        sess = json.loads((session / "session.json").read_text(encoding="utf-8"))
        sess["packet_hash"] = packet_hash
        (session / "session.json").write_text(json.dumps(sess, indent=2) + "\n", encoding="utf-8")
        (session / "evidence").mkdir(exist_ok=True)
        (session / "evidence" / "routing.json").write_text(
            json.dumps({"seats": ["shanks", "blackbeard", "buggy"], "risk_band": "medium"}) + "\n",
            encoding="utf-8",
        )
        return session

    def test_hybrid_happy_path_shared_packet_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            (repo / "WIP.md").write_text("already dirty user work\n", encoding="utf-8")

            session = self._boot_session(repo)
            packet_hash = json.loads((session / "packet.meta.json").read_text())["packet_hash"]

            # Cursor seat: dispatch only
            cursor = invoke_seat(session, "shanks", workdir=repo)
            self.assertEqual(cursor["runtime"], "cursor")
            self.assertTrue(cursor["awaiting_chair_dispatch"])
            inv_c = json.loads((session / "runtime" / "shanks" / "invocation.json").read_text())
            self.assertEqual(inv_c["packet_hash"], packet_hash)

            payload = {
                "findings": [],
                "notes": [],
                "repos_reviewed": ["demo"],
                "attack_card": "Attack card:\n- n/a",
                "disposition": "Content",
            }

            def ok_run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    with mock.patch(
                        "lib.runtime.invoke_seat.invoke_opencode_seat",
                        side_effect=lambda inv: invoke_opencode_seat(
                            {**inv, "workdir": str(repo)}, run_fn=ok_run
                        ),
                    ):
                        bb = invoke_seat(session, "blackbeard", workdir=repo, execute=True)
                        buggy = invoke_seat(session, "buggy", workdir=repo, execute=True)

            self.assertEqual(bb["runtime"], "opencode")
            self.assertEqual(buggy["runtime"], "opencode")
            self.assertTrue(bb.get("completed") or bb.get("schema_valid"))
            self.assertTrue(buggy.get("completed") or buggy.get("schema_valid"))

            inv_bb = json.loads((session / "runtime" / "blackbeard" / "invocation.json").read_text())
            inv_bu = json.loads((session / "runtime" / "buggy" / "invocation.json").read_text())
            self.assertEqual(inv_bb["packet_hash"], packet_hash)
            self.assertEqual(inv_bu["packet_hash"], packet_hash)
            self.assertEqual(inv_bb["packet_hash"], inv_c["packet_hash"])

            meta_bb = json.loads(
                (session / "runtime" / "blackbeard" / "prompt.meta.json").read_text()
            )
            meta_bu = json.loads((session / "runtime" / "buggy" / "prompt.meta.json").read_text())
            self.assertEqual(meta_bb["sharedPrefixHash"], meta_bu["sharedPrefixHash"])
            self.assertNotEqual(meta_bb["fullPromptHash"], meta_bu["fullPromptHash"])
            self.assertEqual(bb["prompt"]["sharedPrefixHash"], meta_bb["sharedPrefixHash"])
            self.assertTrue((session / "runtime" / "shanks" / "prompt.txt").is_file())

            # Validate findings through existing harness
            for seat in ("blackbeard", "buggy"):
                findings = session / "runtime" / seat / "findings.json"
                self.assertTrue(findings.is_file())
                v = subprocess.run(
                    [
                        str(SCRIPTS / "validate-artifact.sh"),
                        "--kind",
                        "findings",
                        "--file",
                        str(findings),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

            # Pre-existing dirt still present; no illicit source files
            self.assertTrue((repo / "WIP.md").is_file())
            self.assertFalse((repo / "src_touched.txt").exists())

            # Marker change mid-session must not mutate freeze
            marker_profile = rp.load_profile("cursor-max")
            freeze2 = rp.freeze_profile_into_session(session, marker_profile, force=False)
            self.assertEqual(freeze2["executionProfile"], "cursor-opencode-go")

    def test_opencode_missing_is_runtime_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": "miss",
                        "review_type": "implementation",
                        "packet_hash": "h",
                        "execution_profile": {
                            "frozen": True,
                            "executionProfile": "cursor-opencode-go",
                            "profile_fingerprint": "x",
                            "seats": [
                                {
                                    "seat": "blackbeard",
                                    "runtime": "opencode",
                                    "model": "opencode/deepseek-v4-flash",
                                    "read_only": True,
                                }
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (session / "packet.md").write_text("p\n", encoding="utf-8")
            (session / "packet.meta.json").write_text(
                json.dumps({"packet_hash": "h"}) + "\n", encoding="utf-8"
            )
            (session / "evidence").mkdir()
            (session / "evidence" / "routing.json").write_text(
                json.dumps({"seats": ["blackbeard"]}) + "\n", encoding="utf-8"
            )
            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(False, "missing")):
                result = invoke_seat(session, "blackbeard", execute=True)
            self.assertEqual(result["failure_category"], "runtime_not_installed")
            self.assertFalse(result["completed"])

    def test_malformed_output_marks_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "sess"
            repo = Path(td) / "repo"
            session.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            (session / "session.json").write_text(
                json.dumps({"session_id": "bad", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            inv = {
                "schema_version": 1,
                "session_id": "bad",
                "session_dir": str(session),
                "review_type": "implementation",
                "seat": "blackbeard",
                "runtime": "opencode",
                "model": "opencode/deepseek-v4-flash",
                "packet_path": str(session / "packet.md"),
                "packet_hash": "h",
                "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
                "schema_path": str(SKILL / "contracts" / "finding.schema.json"),
                "output_path": str(session / "runtime" / "blackbeard" / "findings.json"),
                "timeout_sec": 30,
                "permissions": {"read": True, "write": False},
                "workdir": str(repo),
                "runtime_options": {},
            }
            (session / "packet.md").write_text("p\n", encoding="utf-8")

            def bad(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout="not-json", stderr="")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    result = invoke_opencode_seat(inv, run_fn=bad)
            self.assertEqual(result["failure_category"], "malformed_output")
            self.assertFalse(result["completed"])


if __name__ == "__main__":
    unittest.main()
