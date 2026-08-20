#!/usr/bin/env python3
"""Prompt prefix stability: determinism, ordering, seat-independence, repair, observability.

No paid inference. Fake/stub only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.runtime.prompt_builder import (  # noqa: E402
    PROMPT_FORMAT_VERSION,
    build_reviewer_prompt,
    default_repair_instruction,
    prompt_observability,
)
from lib.runtime.opencode_adapter import invoke_opencode_seat  # noqa: E402


class PromptPrefixStabilitySmoke(unittest.TestCase):
    def _fixture_paths(self, td: Path) -> tuple[Path, Path, str]:
        packet = td / "packet.md"
        schema = SKILL / "contracts" / "finding.schema.json"
        packet.write_text(
            "=== YONKO DOCKET ===\nStable packet.\n\n=== DIFF: a ===\n+x\n",
            encoding="utf-8",
        )
        return packet, schema, "pkt-stable-hash-1"

    def test_determinism_same_inputs_same_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            packet, schema, ph = self._fixture_paths(td)
            a = build_reviewer_prompt(
                packet_path=packet, packet_hash=ph, schema_path=schema, seat="blackbeard"
            )
            b = build_reviewer_prompt(
                packet_path=packet, packet_hash=ph, schema_path=schema, seat="blackbeard"
            )
            self.assertEqual(a["shared_prefix_hash"], b["shared_prefix_hash"])
            self.assertEqual(a["full_prompt_hash"], b["full_prompt_hash"])
            self.assertEqual(a["prompt"], b["prompt"])
            self.assertEqual(a["prompt_format_version"], PROMPT_FORMAT_VERSION)

    def test_section_order_protocol_packet_schema_then_seat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            packet, schema, ph = self._fixture_paths(td)
            built = build_reviewer_prompt(
                packet_path=packet, packet_hash=ph, schema_path=schema, seat="buggy"
            )
            prompt = built["prompt"]
            i_proto = prompt.index("=== SECTION protocol ===")
            i_packet = prompt.index("=== SECTION packet")
            i_schema = prompt.index("=== SECTION finding_schema ===")
            i_seat = prompt.index("=== SECTION seat ===")
            i_run = prompt.index("=== SECTION run ===")
            self.assertLess(i_proto, i_packet)
            self.assertLess(i_packet, i_schema)
            self.assertLess(i_schema, i_seat)
            self.assertLess(i_seat, i_run)
            # Absolute paths must not appear in the shared prefix.
            shared = built["shared_prefix"]
            self.assertNotIn(str(packet), shared)
            self.assertNotIn(str(schema), shared)
            self.assertNotIn("Seat identity:", shared)

    def test_seats_share_prefix_differ_on_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            packet, schema, ph = self._fixture_paths(td)
            bb = build_reviewer_prompt(
                packet_path=packet, packet_hash=ph, schema_path=schema, seat="blackbeard"
            )
            bu = build_reviewer_prompt(
                packet_path=packet, packet_hash=ph, schema_path=schema, seat="buggy"
            )
            self.assertEqual(bb["shared_prefix_hash"], bu["shared_prefix_hash"])
            self.assertNotEqual(bb["full_prompt_hash"], bu["full_prompt_hash"])

    def test_repair_keeps_shared_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            packet, schema, ph = self._fixture_paths(td)
            first = build_reviewer_prompt(
                packet_path=packet, packet_hash=ph, schema_path=schema, seat="blackbeard", attempt=1
            )
            repair = build_reviewer_prompt(
                packet_path=packet,
                packet_hash=ph,
                schema_path=schema,
                seat="blackbeard",
                attempt=2,
                repair_instruction=default_repair_instruction(),
                validation_errors="missing findings array",
            )
            self.assertEqual(first["shared_prefix_hash"], repair["shared_prefix_hash"])
            self.assertNotEqual(first["full_prompt_hash"], repair["full_prompt_hash"])
            self.assertIn("Validation errors:", repair["variable_suffix"])
            self.assertIn("Attempt: 2", repair["variable_suffix"])

    def test_observability_cache_hit_only_from_provider_read(self) -> None:
        built = {
            "prompt_format_version": 1,
            "shared_prefix_hash": "abc",
            "full_prompt_hash": "def",
            "shared_prefix_bytes": 10,
            "full_prompt_bytes": 20,
        }
        none = prompt_observability(built)
        self.assertIsNone(none["cacheHit"])
        self.assertFalse(none["cacheMetricsAvailable"])

        miss = prompt_observability(
            built, usage={"tokens": {"cache_read": 0, "cache_write": 5}}
        )
        self.assertTrue(miss["cacheMetricsAvailable"])
        self.assertFalse(miss["cacheHit"])

        hit = prompt_observability(
            built, usage={"tokens": {"cache_read": 100, "cache_write": 0}}
        )
        self.assertTrue(hit["cacheHit"])

    def test_opencode_adapter_writes_prompt_meta_and_prompt_on_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            session = td / "sess"
            repo = td / "repo"
            session.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            packet = session / "packet.md"
            packet.write_text("=== DIFF: x ===\n+1\n", encoding="utf-8")
            schema = SKILL / "contracts" / "finding.schema.json"
            (session / "session.json").write_text(
                json.dumps({"session_id": "p1", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            payload = {
                "findings": [],
                "notes": [],
                "repos_reviewed": ["demo"],
                "attack_card": "Attack card:\n- n/a",
                "disposition": "Content",
            }

            def ok_run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

            inv = {
                "schema_version": 1,
                "session_id": "p1",
                "session_dir": str(session),
                "review_type": "implementation",
                "seat": "blackbeard",
                "runtime": "opencode",
                "model": "opencode-go/deepseek-v4-pro",
                "model_configured": "opencode-go/deepseek-v4-pro",
                "model_resolved": "opencode-go/deepseek-v4-pro",
                "packet_path": str(packet),
                "packet_hash": "h1",
                "prompt_path": str(session / "prompt.md"),
                "schema_path": str(schema),
                "output_path": str(session / "runtime" / "blackbeard" / "findings.json"),
                "timeout_sec": 30,
                "permissions": {"read": True, "write": False},
                "workdir": str(repo),
            }
            with mock.patch(
                "lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")
            ):
                with mock.patch(
                    "lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")
                ):
                    result = invoke_opencode_seat(inv, run_fn=ok_run)

            out_dir = session / "runtime" / "blackbeard"
            self.assertTrue((out_dir / "prompt.txt").is_file())
            meta = json.loads((out_dir / "prompt.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["promptFormatVersion"], 1)
            self.assertTrue(meta["sharedPrefixHash"])
            self.assertIn("prompt", result)
            self.assertEqual(result["prompt"]["sharedPrefixHash"], meta["sharedPrefixHash"])
            prompt_text = (out_dir / "prompt.txt").read_text(encoding="utf-8")
            self.assertLess(
                prompt_text.index("=== SECTION protocol ==="),
                prompt_text.index("=== SECTION seat ==="),
            )


if __name__ == "__main__":
    unittest.main()
