#!/usr/bin/env python3
"""Tests for config/model-selections.json as the single source of truth."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
import sys

sys.path.insert(0, str(SCRIPTS))

from lib.runtime import resolve_profile as rp  # noqa: E402
from lib.runtime.model_selections import (  # noqa: E402
    alternate_ids,
    apply_panel_to_profile,
    load_model_selections,
    seat_selection,
)


class ModelSelectionsTests(unittest.TestCase):
    def test_load_defaults_panel(self) -> None:
        data = load_model_selections()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["version"], "2026-08-04")
        chair = seat_selection("cursor-opencode-go", "chair", data)
        self.assertEqual(chair["configured"], "auto")
        self.assertEqual(chair["resolve_mode"], "literal")
        bb = seat_selection("cursor-opencode-go", "blackbeard", data)
        self.assertEqual(bb["configured"], "opencode-go/deepseek-v4-flash")
        buggy = seat_selection("cursor-opencode-go", "buggy", data)
        self.assertEqual(buggy["configured"], "opencode-go/gpt-5.6-luna")
        luffy = seat_selection("cursor-opencode-go", "luffy", data)
        self.assertEqual(luffy["configured"], "opencode-go/qwen3.7-plus")
        self.assertEqual(luffy["activation"], "escalation_only")

    def test_alternates_documented_not_auto_applied(self) -> None:
        alts = alternate_ids()
        self.assertEqual(alts["blackbeard_pro"], "opencode-go/deepseek-v4-pro")
        self.assertEqual(alts["luffy_kimi"], "opencode-go/kimi-k3")
        self.assertNotIn("buggy_qwen", alts)
        profile = rp.load_profile("cursor-opencode-go")
        self.assertNotEqual(
            profile["seats"]["blackbeard"]["model"]["configured"],
            alts["blackbeard_pro"],
        )
        self.assertNotEqual(
            profile["seats"]["luffy"]["model"]["configured"],
            alts["luffy_kimi"],
        )

    def test_optional_pro_selection_via_edit(self) -> None:
        profile = rp.load_profile("cursor-opencode-go")
        profile = json.loads(json.dumps(profile))
        profile["seats"]["blackbeard"]["model"]["configured"] = "opencode-go/deepseek-v4-pro"
        mid = rp.match_opencode_model(
            profile["seats"]["blackbeard"],
            ["opencode-go/deepseek-v4-flash", "opencode-go/deepseek-v4-pro"],
        )
        self.assertEqual(mid, "opencode-go/deepseek-v4-pro")
    def test_optional_qwen_selection_via_edit(self) -> None:
        profile = rp.load_profile("cursor-opencode-go")
        profile = json.loads(json.dumps(profile))
        profile["seats"]["buggy"]["model"]["configured"] = "opencode-go/qwen3.7-plus"
        mid = rp.match_opencode_model(
            profile["seats"]["buggy"],
            ["opencode-go/qwen3.7-plus", "opencode-go/gpt-5.6-luna"],
        )
        self.assertEqual(mid, "opencode-go/qwen3.7-plus")

    def test_no_silent_fallback_when_configured_missing(self) -> None:
        with self.assertRaises(rp.ProfileError) as ctx:
            rp.match_opencode_model(
                {"model": {"configured": "opencode-go/deepseek-v4-pro"}},
                ["opencode-go/deepseek-v4-flash"],
            )
        self.assertEqual(ctx.exception.category, "model_unavailable")
        self.assertIn("no silent substitute", ctx.exception.message)

    def test_ambiguous_substring_fails(self) -> None:
        with self.assertRaises(rp.ProfileError) as ctx:
            rp.match_opencode_model(
                {"model": {"match_substrings": ["deepseek", "v4"]}},
                ["opencode-go/deepseek-v4-flash", "opencode-go/deepseek-v4-pro"],
            )
        self.assertIn("ambiguous", ctx.exception.message)

    def test_freeze_records_selection_version_and_models(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "session.json").write_text(
                json.dumps({"session_id": "ms1", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            freeze = rp.freeze_profile_into_session(
                session, rp.load_profile("cursor-opencode-go"), force=True
            )
            self.assertEqual(freeze["model_selection_version"], "2026-08-04")
            self.assertEqual(freeze["model_selection_panel"], "cursor-opencode-go")
            by = {r["seat"]: r for r in freeze["seats"]}
            self.assertEqual(by["chair"]["configured_model"], "auto")
            self.assertEqual(by["chair"]["resolved_model"], "auto")
            self.assertEqual(by["shanks"]["resolved_model"], "grok")
            self.assertEqual(by["blackbeard"]["resolved_model"], "opencode-go/deepseek-v4-flash")
            self.assertEqual(by["buggy"]["resolved_model"], "opencode-go/gpt-5.6-luna")
            self.assertEqual(by["luffy"]["resolved_model"], "opencode-go/qwen3.7-plus")
            self.assertEqual(by["luffy"]["activation"], "escalation_only")

            # Mid-session config change must not alter freeze
            selections = load_model_selections()
            selections = json.loads(json.dumps(selections))
            selections["panels"]["cursor-opencode-go"]["seats"]["blackbeard"][
                "configured"
            ] = "opencode-go/deepseek-v4-pro"
            # re-freeze without force keeps original
            freeze2 = rp.freeze_profile_into_session(
                session, rp.load_profile("cursor-opencode-go"), force=False
            )
            by2 = {r["seat"]: r for r in freeze2["seats"]}
            self.assertEqual(by2["blackbeard"]["resolved_model"], "opencode-go/deepseek-v4-flash")

    def test_apply_panel_rejects_runtime_mismatch(self) -> None:
        raw = json.loads(
            (SKILL / "config" / "execution-profiles" / "cursor-opencode-go.json").read_text()
        )
        raw["seats"]["blackbeard"]["runtime"] = "cursor"
        with self.assertRaises(rp.ProfileError):
            apply_panel_to_profile(raw)


if __name__ == "__main__":
    unittest.main()
