#!/usr/bin/env python3
"""Seat-council prepare / require-complete / OpenCode execute guard / compact packet budget."""

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
sys.path.insert(0, str(SCRIPTS / "workflow"))

from lib.assemble_packet import compact_evidence_graph  # noqa: E402
from lib.runtime import seat_council as sc  # noqa: E402
import guards as gu  # noqa: E402
import state as st  # noqa: E402


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, (dict, list)):
        path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(str(obj), encoding="utf-8")


class SeatCouncilSmoke(unittest.TestCase):
    def test_prepare_invokes_without_execute(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "evidence").mkdir()
            _write(session / "evidence" / "routing.json", {"seats": ["shanks", "blackbeard"]})
            calls: list[dict] = []

            def fake_invoke(session_dir, seat, execute=False, **kwargs):
                calls.append({"seat": seat, "execute": execute})
                runtime = "cursor" if seat == "shanks" else "opencode"
                rt = session_dir / "runtime" / seat
                rt.mkdir(parents=True, exist_ok=True)
                _write(
                    rt / "dispatch.json",
                    {
                        "runtime": runtime,
                        "task_description": f"{seat.title()} Yonko review",
                        "execute_command": (
                            f"invoke-seat.sh --session {session_dir} --seat {seat} --execute"
                            if runtime == "opencode"
                            else None
                        ),
                    },
                )
                _write(
                    rt / "result.json",
                    {
                        "runtime": runtime,
                        "completed": runtime == "cursor",
                        "awaiting_chair_dispatch": runtime == "opencode",
                    },
                )
                return {
                    "completed": runtime == "cursor",
                    "awaiting_chair_dispatch": runtime == "opencode",
                    "runtime": runtime,
                }

            with mock.patch.object(sc, "invoke_seat", side_effect=fake_invoke):
                council = sc.prepare(session)

            self.assertEqual(
                [{"seat": "shanks", "execute": False}, {"seat": "blackbeard", "execute": False}],
                calls,
            )
            self.assertTrue((session / "council.json").is_file())
            spawn = council["task_spawn_order"]
            self.assertEqual(2, len(spawn))
            # OpenCode must kick first (parallel wrappers before Cursor seats)
            self.assertEqual("blackbeard", spawn[0]["seat"])
            self.assertEqual("opencode_first", spawn[0]["spawn_priority"])
            self.assertTrue(spawn[0]["run_in_background"])
            self.assertIn("--execute", spawn[0]["execute_command"] or "")
            prompt = spawn[0].get("wrapper_prompt") or ""
            self.assertIn("parent --kickoff", prompt)
            self.assertIn("Shell", prompt)
            self.assertEqual("shanks", spawn[1]["seat"])
            self.assertEqual("cursor", spawn[1]["spawn_priority"])
            self.assertTrue(council["kickoff"]["opencode_first"])
            self.assertTrue(council["kickoff"]["parent_starts_opencode"])

            report = sc.require_complete(session)
            self.assertFalse(report["ok"])
            self.assertEqual(["blackbeard"], report["incomplete_seats"])
            self.assertEqual("OPENCODE_EXECUTE_MISSING", report["failure_code"])
            self.assertTrue(
                any(r["seat"] == "blackbeard" and r.get("never_started") for r in report["seats"])
            )

            # After findings land, require-complete passes
            _write(session / "runtime" / "blackbeard" / "findings.json", {"findings": []})
            _write(
                session / "runtime" / "blackbeard" / "result.json",
                {
                    "runtime": "opencode",
                    "completed": True,
                    "awaiting_chair_dispatch": False,
                    "schema_valid": True,
                    "failure_category": None,
                },
            )
            _write(session / "runtime" / "shanks" / "findings.json", {"findings": []})
            report2 = sc.require_complete(session)
            self.assertTrue(report2["ok"])

            # Schema-invalid OpenCode output must not count as complete
            _write(
                session / "runtime" / "blackbeard" / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": False,
                    "schema_valid": False,
                    "failure_category": "schema_validation_failure",
                },
            )
            report3 = sc.require_complete(session)
            self.assertFalse(report3["ok"])
            self.assertIn("blackbeard", report3["incomplete_seats"])

    def test_execute_awaiting_runs_never_started_opencode_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "evidence").mkdir()
            _write(
                session / "evidence" / "routing.json",
                {"seats": ["blackbeard", "buggy", "luffy"]},
            )
            for seat in ("blackbeard", "buggy", "luffy"):
                rt = session / "runtime" / seat
                rt.mkdir(parents=True)
                _write(
                    rt / "dispatch.json",
                    {
                        "runtime": "opencode",
                        "execute_command": (
                            f"invoke-seat.sh --session {session} --seat {seat} --execute"
                        ),
                    },
                )
                _write(
                    rt / "result.json",
                    {
                        "runtime": "opencode",
                        "completed": False,
                        "awaiting_chair_dispatch": True,
                        "attempts": 0,
                    },
                )

            started: list[str] = []

            def fake_invoke(session_dir, seat, execute=False, **kwargs):
                self.assertTrue(execute)
                started.append(seat)
                rt = session_dir / "runtime" / seat
                _write(rt / "findings.json", {"plan_findings": []})
                _write(
                    rt / "result.json",
                    {
                        "runtime": "opencode",
                        "completed": True,
                        "awaiting_chair_dispatch": False,
                        "attempts": 1,
                        "schema_valid": True,
                        "duration_ms": 10,
                    },
                )
                return {
                    "completed": True,
                    "awaiting_chair_dispatch": False,
                    "schema_valid": True,
                    "duration_ms": 10,
                }

            with mock.patch.object(sc, "invoke_seat", side_effect=fake_invoke):
                out = sc.execute_awaiting(session)

            self.assertEqual(set(started), {"blackbeard", "buggy", "luffy"})
            self.assertEqual(set(out["executed"]), {"blackbeard", "buggy", "luffy"})
            self.assertTrue(out["ok"])
            self.assertTrue((session / "council-execute-awaiting.json").is_file())

    def test_execute_awaiting_skips_live_pid_recovers_abandoned(self) -> None:
        """Live execute.pid skipped; dead mid-flight (abandoned) is recoverable."""
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "evidence").mkdir()
            _write(
                session / "evidence" / "routing.json",
                {"seats": ["blackbeard", "buggy", "luffy"]},
            )
            for seat in ("blackbeard", "buggy", "luffy"):
                rt = session / "runtime" / seat
                rt.mkdir(parents=True)
                _write(
                    rt / "dispatch.json",
                    {
                        "runtime": "opencode",
                        "execute_command": (
                            f"invoke-seat.sh --session {session} --seat {seat} --execute"
                        ),
                    },
                )

            # Starved: prepare-only
            _write(
                session / "runtime" / "luffy" / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": True,
                    "attempts": 0,
                },
            )
            # Truly in-flight: live execute.pid (this test process)
            _write(
                session / "runtime" / "blackbeard" / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": False,
                    "attempts": 1,
                    "execute_in_progress": True,
                },
            )
            (session / "runtime" / "blackbeard" / "execute.pid").write_text(
                str(os.getpid()), encoding="utf-8"
            )
            # Abandoned: attempts>=1, flag set, no live pid
            _write(
                session / "runtime" / "buggy" / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": False,
                    "attempts": 1,
                    "execute_in_progress": True,
                },
            )
            # Garbage findings must not block recovery
            _write(
                session / "runtime" / "buggy" / "findings.json",
                {"type": "step_start", "part": {"type": "step-start"}},
            )

            started: list[str] = []

            def fake_invoke(session_dir, seat, execute=False, **kwargs):
                self.assertTrue(execute)
                started.append(seat)
                rt = session_dir / "runtime" / seat
                _write(rt / "findings.json", {"plan_findings": []})
                _write(
                    rt / "result.json",
                    {
                        "runtime": "opencode",
                        "completed": True,
                        "awaiting_chair_dispatch": False,
                        "attempts": 1,
                        "schema_valid": True,
                        "duration_ms": 10,
                    },
                )
                return {
                    "completed": True,
                    "schema_valid": True,
                    "duration_ms": 10,
                }

            with mock.patch.object(sc, "invoke_seat", side_effect=fake_invoke):
                out = sc.execute_awaiting(session)

            self.assertEqual(set(started), {"luffy", "buggy"})
            self.assertEqual(set(out["executed"]), {"luffy", "buggy"})
            self.assertTrue(out["ok"])

            report = sc.status_report(session)
            by_seat = {r["seat"]: r for r in report["seats"]}
            self.assertFalse(by_seat["blackbeard"].get("never_started"))
            self.assertTrue(by_seat["blackbeard"].get("execute_in_progress"))
            self.assertFalse(by_seat["buggy"].get("never_started"))  # recovered
            self.assertTrue(by_seat["luffy"]["has_findings"])

    def test_kickoff_detaches_never_started_seats(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "evidence").mkdir()
            _write(session / "evidence" / "routing.json", {"seats": ["blackbeard"]})
            rt = session / "runtime" / "blackbeard"
            rt.mkdir(parents=True)
            _write(
                rt / "dispatch.json",
                {
                    "runtime": "opencode",
                    "execute_command": "invoke-seat.sh --session X --seat blackbeard --execute",
                },
            )
            _write(
                rt / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": True,
                    "attempts": 0,
                },
            )

            class FakeProc:
                pid = 424242

            with mock.patch.object(sc.subprocess, "Popen", return_value=FakeProc()) as popen:
                out = sc.kickoff_opencode(session, background=True)

            self.assertTrue(out["ok"])
            self.assertEqual(out["started"][0]["seat"], "blackbeard")
            self.assertEqual(out["started"][0]["pid"], 424242)
            self.assertTrue((rt / "kickoff.pid").is_file())
            self.assertFalse((rt / "execute.pid").is_file())
            popen.assert_called_once()
            self.assertTrue((session / "council-kickoff.json").is_file())

            # Without a live kickoff pid, status treats soft marker as abandoned/never_started
            # (FakeProc pid is not alive) - that is correct for recovery.
            status = sc._seat_status(session, "blackbeard")
            self.assertTrue(status["abandoned"] or status["never_started"])

    def test_garbage_findings_not_has_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "evidence").mkdir()
            _write(session / "evidence" / "routing.json", {"seats": ["blackbeard"]})
            rt = session / "runtime" / "blackbeard"
            rt.mkdir(parents=True)
            _write(
                rt / "dispatch.json",
                {"runtime": "opencode", "execute_command": "invoke-seat.sh --execute"},
            )
            _write(
                rt / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": False,
                    "attempts": 1,
                    "execute_in_progress": True,
                },
            )
            _write(rt / "findings.json", {"type": "step_start"})
            status = sc._seat_status(session, "blackbeard")
            self.assertFalse(status["has_findings"])
            self.assertTrue(status["never_started"])

    def test_opencode_execute_missing_blocks_finalize_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "evidence").mkdir()
            _write(session / "evidence" / "routing.json", {"seats": ["blackbeard"]})
            rt = session / "runtime" / "blackbeard"
            rt.mkdir(parents=True)
            _write(
                rt / "result.json",
                {
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": True,
                },
            )
            _write(
                rt / "dispatch.json",
                {
                    "runtime": "opencode",
                    "execute_command": "invoke-seat.sh --session X --seat blackbeard --execute",
                },
            )
            self.assertTrue(gu._opencode_execute_missing(session))
            self.assertIn("OPENCODE_EXECUTE_MISSING", st.FAILURE_CODES)

            _write(rt / "findings.json", {"findings": []})
            self.assertFalse(gu._opencode_execute_missing(session))

    def test_compact_evidence_graph_budget_and_keeps_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            graph_path = Path(td) / "evidence-graph.json"
            huge = {
                "risk_band": "critical",
                "metrics": {
                    "nodes": 500,
                    "edges": 1200,
                    "changed_symbols": 3,
                },
                "changed_symbols": [
                    {
                        "change_kind": "modified",
                        "repository": "st",
                        "path": "TotalRateCalculator.java",
                        "name": "calculateCombinedFreightRateAndBaf",
                        "confidence": 0.9,
                    },
                    {
                        "change_kind": "modified",
                        "repository": "st",
                        "path": "InvoiceRepositoryExtension.java",
                        "name": "combineBafLineItemCategories",
                        "confidence": 0.9,
                    },
                    {
                        "change_kind": "modified",
                        "repository": "fe",
                        "path": "contractsColumnOptions.ts",
                        "name": "options",
                        "confidence": 0.7,
                    },
                ],
                "categories": {"money": 2, "label": 1},
                "nodes": [{"id": i, "blob": "x" * 200} for i in range(400)],
                "edges": [{"from": i, "to": i + 1, "blob": "y" * 100} for i in range(399)],
                "unresolved_edges": [],
            }
            graph_path.write_text(json.dumps(huge), encoding="utf-8")
            full_bytes = graph_path.stat().st_size
            compact = compact_evidence_graph(graph_path)
            self.assertLess(len(compact.encode("utf-8")), full_bytes // 5)
            self.assertLess(len(compact.encode("utf-8")), 16_000)
            self.assertIn("calculateCombinedFreightRateAndBaf", compact)
            self.assertIn("combineBafLineItemCategories", compact)
            self.assertIn("Compact Evidence Graph", compact)
            self.assertNotIn('"nodes":', compact)


if __name__ == "__main__":
    unittest.main()
