#!/usr/bin/env python3
"""Smoke: review-quality ledger record / annotate / rollup (observational only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(SCRIPTS))

from lib.review_quality_ledger import (  # noqa: E402
    annotate_human,
    build_row,
    load_ledger,
    upsert_row,
    write_rollup,
)


class ReviewQualityLedgerSmoke(unittest.TestCase):
    def _session(self, root: Path) -> Path:
        session = root / "sess-rq-1"
        session.mkdir()
        (session / "session.json").write_text(
            json.dumps(
                {
                    "session_id": "sess-rq-1",
                    "review_type": "implementation",
                    "packet_hash": "abc",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (session / "evidence").mkdir()
        (session / "evidence" / "risk.json").write_text(
            json.dumps({"risk": "critical", "reviewers": 4}) + "\n", encoding="utf-8"
        )
        (session / "evidence" / "routing.json").write_text(
            json.dumps({"seats": ["shanks", "blackbeard", "buggy"]}) + "\n",
            encoding="utf-8",
        )

        def write_seat(seat: str, title: str, cost: float) -> None:
            rt = session / "runtime" / seat
            rt.mkdir(parents=True)
            (rt / "findings.json").write_text(
                json.dumps(
                    {
                        "disposition": "Remand",
                        "findings": [
                            {
                                "id": f"{seat}-1",
                                "reviewer": seat,
                                "category": "correctness",
                                "severity": "high",
                                "title": title,
                                "claim": "claim",
                                "locus": {"repository": "demo", "path": "A.java"},
                                "evidence": "diff",
                                "reachability": "yes",
                                "impact": "money",
                                "confidence": "high",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (rt / "result.json").write_text(
                json.dumps(
                    {
                        "seat": seat,
                        "runtime": "opencode" if seat != "shanks" else "cursor",
                        "model_actual": f"model-{seat}",
                        "completed": True,
                        "schema_valid": True,
                        "duration_ms": 1000 if seat == "buggy" else 5000,
                        "usage": {"cost": cost, "tokens": {"total": 10}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        # Same title on two seats -> duplicate cluster
        write_seat("shanks", "Stale combined amount", 0.0)
        write_seat("blackbeard", "Stale combined amount", 0.01)
        write_seat("buggy", "Missing ets sibling test", 0.005)

        (session / "findings.json").write_text(
            json.dumps(
                {
                    "accepted": [
                        {
                            "id": "A1",
                            "reviewer": "shanks",
                            "category": "correctness",
                            "severity": "medium",
                            "original_severity": "high",
                            "title": "Stale combined amount",
                            "locus": {"repository": "demo", "path": "A.java"},
                        }
                    ],
                    "dropped": [
                        {
                            "id": "D1",
                            "reviewer": "buggy",
                            "category": "test-gap",
                            "severity": "low",
                            "title": "noise",
                            "locus": {"repository": "demo", "path": "B.java"},
                        }
                    ],
                    "held": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (session / "verification.json").write_text(
            json.dumps(
                [
                    {"verdict": "confirmed"},
                    {"verdict": "rejected"},
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return session

    def test_record_annotate_rollup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sessions = root / "sessions"
            sessions.mkdir()
            session = self._session(sessions)

            row = build_row(session)
            self.assertEqual(row["session_id"], "sess-rq-1")
            self.assertEqual(row["findings"]["duplicate_cross_seat_count"], 1)
            self.assertEqual(row["findings"]["unique_accepted_by_seat"]["shanks"], 1)
            self.assertEqual(row["chair"]["reject_rate_percent"], 50.0)
            self.assertEqual(row["verifier"]["reject_rate_percent"], 50.0)
            self.assertEqual(row["severity_change_count"], 1)
            self.assertIn("human_reached_production_unset", row["gaps"])

            ledger = upsert_row(sessions, row)
            self.assertTrue(ledger.is_file())
            self.assertTrue((session / "review-quality.json").is_file())

            annotate_human(
                session,
                reached_production="no",
                reviewer_found_human_missed="yes",
                notes="caught underbill humans missed",
            )
            row2 = build_row(session)
            upsert_row(sessions, row2)
            self.assertEqual(row2["human"]["reached_production"], "no")
            self.assertEqual(row2["human"]["reviewer_found_human_missed"], "yes")
            self.assertNotIn("human_reached_production_unset", row2["gaps"])

            # Upsert replaces same session_id
            upsert_row(sessions, row2)
            self.assertEqual(len(load_ledger(sessions)), 1)

            rollup = write_rollup(sessions)
            self.assertEqual(rollup["sessions"], 1)
            self.assertEqual(rollup["duplicate_cross_seat_total"], 1)
            self.assertTrue((sessions / "_rollup" / "review-quality-rollup.md").is_file())


if __name__ == "__main__":
    unittest.main()
