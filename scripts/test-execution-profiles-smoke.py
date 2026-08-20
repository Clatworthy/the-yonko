#!/usr/bin/env python3
"""Smoke tests for execution profiles, runtime dispatch, doctor, and OpenCode adapter stubs."""

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

from lib.runtime import resolve_profile as rp  # noqa: E402
from lib.runtime.cursor_adapter import invoke_cursor_seat  # noqa: E402
from lib.runtime.invoke_seat import build_invocation, invoke_seat  # noqa: E402
from lib.runtime.normalise_result import (  # noqa: E402
    extract_findings_from_opencode_stdout,
    extract_json_candidate,
    extract_usage_from_opencode_stdout,
    redact_secrets,
)
from lib.runtime.opencode_adapter import invoke_opencode_seat  # noqa: E402
from lib.runtime.doctor import doctor  # noqa: E402

FAKE_OC = SCRIPTS / "fixtures" / "runtime" / "fake-opencode"


def _run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


class ProfileResolutionTests(unittest.TestCase):
    def test_missing_marker_defaults_to_cursor_standard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "missing.json"
            m = rp.read_marker(marker)
            self.assertEqual(m["executionProfile"], "cursor-standard")

    def test_valid_profiles_load(self) -> None:
        for pid in ("cursor-standard", "cursor-opencode-go", "cursor-max"):
            profile = rp.load_profile(pid)
            self.assertEqual(profile["id"], pid)
            rp.validate_profile(profile)

    def test_unknown_profile_fails(self) -> None:
        with self.assertRaises(rp.ProfileError) as ctx:
            rp.load_profile("no-such-profile")
        self.assertEqual(ctx.exception.category, "invalid_profile")

    def test_malformed_marker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "execution-profile.json"
            marker.write_text("{not json", encoding="utf-8")
            with self.assertRaises(rp.ProfileError):
                rp.read_marker(marker)

    def test_empty_execution_profile_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "execution-profile.json"
            marker.write_text(json.dumps({"executionProfile": ""}) + "\n", encoding="utf-8")
            with self.assertRaises(rp.ProfileError):
                rp.read_marker(marker)

    def test_freeze_and_immutable_on_marker_change(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "sess"
            session.mkdir()
            (session / "session.json").write_text(
                json.dumps({"session_id": "t1", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            marker = Path(td) / "marker.json"
            marker.write_text(
                json.dumps({"schema_version": 1, "executionProfile": "cursor-standard"}) + "\n",
                encoding="utf-8",
            )
            # temporarily point resolve at marker via freeze with explicit profile
            profile = rp.load_profile("cursor-standard")
            freeze1 = rp.freeze_profile_into_session(session, profile)
            self.assertEqual(freeze1["executionProfile"], "cursor-standard")
            self.assertTrue(freeze1["frozen"])
            # change would-be marker; freeze must not mutate
            profile2 = rp.load_profile("cursor-max")
            freeze2 = rp.freeze_profile_into_session(session, profile2, force=False)
            self.assertEqual(freeze2["executionProfile"], "cursor-standard")
            freeze3 = rp.freeze_profile_into_session(session, profile2, force=True)
            self.assertEqual(freeze3["executionProfile"], "cursor-max")

    def test_profile_schema_rejects_missing_runtime(self) -> None:
        bad = rp.load_profile("cursor-standard")
        bad = json.loads(json.dumps(bad))
        del bad["seats"]["shanks"]["runtime"]
        with self.assertRaises(rp.ProfileError):
            rp.validate_profile(bad)

    def test_profile_schema_rejects_unsupported_runtime(self) -> None:
        bad = rp.load_profile("cursor-standard")
        bad = json.loads(json.dumps(bad))
        bad["seats"]["shanks"]["runtime"] = "ollama"
        with self.assertRaises(rp.ProfileError):
            rp.validate_profile(bad)

    def test_profile_schema_rejects_fallback(self) -> None:
        bad = rp.load_profile("cursor-standard")
        bad = json.loads(json.dumps(bad))
        bad["fallback_policy"] = "cursor"
        with self.assertRaises(rp.ProfileError):
            rp.validate_profile(bad)

    def test_missing_required_seat(self) -> None:
        bad = rp.load_profile("cursor-standard")
        bad = json.loads(json.dumps(bad))
        del bad["seats"]["luffy"]
        with self.assertRaises(rp.ProfileError):
            rp.validate_profile(bad)


class RuntimeDispatchTests(unittest.TestCase):
    def _session(self, profile_id: str = "cursor-standard") -> Path:
        td = Path(tempfile.mkdtemp())
        session = td / "sess"
        session.mkdir()
        (session / "session.json").write_text(
            json.dumps(
                {
                    "session_id": "dispatch-1",
                    "review_type": "implementation",
                    "packet_hash": "abc123",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (session / "packet.md").write_text("# packet\n", encoding="utf-8")
        (session / "packet.meta.json").write_text(
            json.dumps({"packet_hash": "abc123"}) + "\n", encoding="utf-8"
        )
        (session / "evidence").mkdir()
        (session / "evidence" / "routing.json").write_text(
            json.dumps({"seats": ["shanks", "blackbeard", "buggy"]}) + "\n",
            encoding="utf-8",
        )
        rp.freeze_profile_into_session(session, rp.load_profile(profile_id))
        return session

    def test_cursor_seat_uses_cursor_adapter(self) -> None:
        session = self._session("cursor-standard")
        result = invoke_seat(session, "shanks")
        self.assertEqual(result["runtime"], "cursor")
        self.assertTrue(result["awaiting_chair_dispatch"])
        self.assertTrue((session / "runtime" / "shanks" / "dispatch.json").is_file())
        inv = json.loads((session / "runtime" / "shanks" / "invocation.json").read_text())
        self.assertEqual(inv["runtime"], "cursor")
        self.assertIn("packet.md", inv["packet_path"])
        self.assertTrue(inv["schema_path"].endswith("finding.schema.json"))

    def test_opencode_go_ladder_runtimes(self) -> None:
        profile = rp.load_profile("cursor-opencode-go")
        self.assertEqual(profile.get("model_selection_panel"), "cursor-opencode-go")
        self.assertEqual(profile["seats"]["chair"]["runtime"], "cursor")
        self.assertEqual(profile["seats"]["chair"]["model"]["configured"], "auto")
        self.assertEqual(profile["seats"]["shanks"]["runtime"], "cursor")
        self.assertEqual(profile["seats"]["shanks"]["model"]["configured"], "grok")
        self.assertEqual(profile["seats"]["blackbeard"]["runtime"], "opencode")
        self.assertEqual(profile["seats"]["buggy"]["runtime"], "opencode")
        self.assertEqual(profile["seats"]["luffy"]["runtime"], "opencode")
        self.assertEqual(
            profile["seats"]["blackbeard"]["model"]["configured"],
            "opencode-go/deepseek-v4-flash",
        )
        self.assertEqual(
            profile["seats"]["buggy"]["model"]["configured"],
            "opencode-go/gpt-5.6-luna",
        )
        self.assertEqual(
            profile["seats"]["luffy"]["model"]["configured"],
            "opencode-go/qwen3.7-plus",
        )
        self.assertEqual(profile["seats"]["luffy"]["model"]["activation"], "escalation_only")
        self.assertEqual(
            profile.get("seat_ladder"),
            ["chair", "shanks", "blackbeard", "buggy", "luffy"],
        )

    def test_opencode_seat_defaults_to_task_dispatch(self) -> None:
        session = self._session("cursor-opencode-go")
        freeze = json.loads((session / "session.json").read_text())
        for row in freeze["execution_profile"]["seats"]:
            if row["seat"] == "blackbeard":
                row["model"] = "opencode-go/deepseek-v4-flash"
                row["configured_model"] = "opencode-go/deepseek-v4-flash"
                row["resolved_model"] = "opencode-go/deepseek-v4-flash"
        (session / "session.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")

        result = invoke_seat(session, "blackbeard")
        self.assertEqual(result["runtime"], "opencode")
        self.assertTrue(result["awaiting_chair_dispatch"])
        dispatch = json.loads(
            (session / "runtime" / "blackbeard" / "dispatch.json").read_text(encoding="utf-8")
        )
        self.assertEqual(dispatch["dispatch_mode"], "cursor_task_wrapper")
        self.assertIn("--execute", dispatch["execute_command"])
        self.assertIn("OpenCode", dispatch["task_description"])
        self.assertIn("Blackbeard", dispatch["task_description"])
        self.assertIn("Shell ONLY", dispatch["instructions"])

    def test_opencode_seat_uses_opencode_adapter(self) -> None:
        session = self._session("cursor-opencode-go")
        # Force configured model to avoid live listing
        freeze = json.loads((session / "session.json").read_text())
        for row in freeze["execution_profile"]["seats"]:
            if row["seat"] == "blackbeard":
                row["model"] = "opencode-go/deepseek-v4-flash"
                row["configured_model"] = "opencode-go/deepseek-v4-flash"
                row["resolved_model"] = "opencode-go/deepseek-v4-flash"
        (session / "session.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")

        def fake_run(args, **kwargs):
            out = session / "runtime" / "blackbeard" / "findings.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "findings": [],
                "notes": [],
                "repos_reviewed": ["x"],
                "attack_card": "n/a",
                "disposition": "Content",
            }
            out.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

        with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
            with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                with mock.patch("lib.runtime.opencode_adapter.run_opencode", side_effect=fake_run):
                    # invoke_opencode_seat uses run_fn via runner lambda that calls run_opencode
                    # Patch at invoke path:
                    with mock.patch(
                        "lib.runtime.invoke_seat.invoke_opencode_seat",
                        side_effect=lambda inv: invoke_opencode_seat(inv, run_fn=fake_run),
                    ):
                        result = invoke_seat(session, "blackbeard", execute=True)
        self.assertEqual(result["runtime"], "opencode")
        self.assertTrue(result["completed"] or result.get("schema_valid") or result.get("output_path"))

    def test_skipped_by_routing(self) -> None:
        session = self._session("cursor-standard")
        result = invoke_seat(session, "luffy")
        self.assertTrue(result["skipped_by_routing"])

    def test_no_provider_branch_in_dispatcher_module(self) -> None:
        src = (SCRIPTS / "lib" / "runtime" / "invoke_seat.py").read_text(encoding="utf-8")
        self.assertNotIn('if profile ==', src)
        self.assertNotIn("cursor-opencode-go", src)


class OpenCodeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(FAKE_OC.is_file())
        FAKE_OC.chmod(FAKE_OC.stat().st_mode | 0o111)

    def test_run_args_message_before_file(self) -> None:
        from lib.runtime.opencode_adapter import build_opencode_run_args

        args = build_opencode_run_args(
            model="opencode-go/deepseek-v4-flash",
            title="yonko-bb",
            prompt="You are a reviewer. Return JSON only.",
            packet_path="/tmp/session/packet.md",
            workdir="/tmp/scratch",
            extra_files=["/tmp/session/schema.json"],
        )
        self.assertEqual(args[0], "run")
        file_idxs = [i for i, a in enumerate(args) if a == "--file"]
        self.assertTrue(file_idxs)
        prompt_idx = args.index("You are a reviewer. Return JSON only.")
        self.assertLess(prompt_idx, file_idxs[0])
        self.assertIn("--dir", args)
        self.assertEqual(args[args.index("--dir") + 1], "/tmp/scratch")

    def test_run_args_attach_prompt_file_keeps_argv_short(self) -> None:
        from lib.runtime.opencode_adapter import (
            PROMPT_FILE_MESSAGE,
            build_opencode_run_args,
            cmdline_length,
            exceeds_windows_cmdline_limit,
        )

        big_prompt = "PACKET LINE\n" * 12_000

        argv_inline = build_opencode_run_args(
            model="opencode-go/deepseek-v4-flash",
            title="yonko-bb",
            prompt=big_prompt,
            packet_path="/tmp/session/packet.md",
            workdir="/tmp/scratch",
        )
        self.assertTrue(exceeds_windows_cmdline_limit(argv_inline))

        argv = build_opencode_run_args(
            model="opencode-go/deepseek-v4-flash",
            title="yonko-bb",
            prompt=big_prompt,
            packet_path="/tmp/session/packet.md",
            workdir="/tmp/scratch",
            extra_files=["/tmp/session/schema.json"],
            prompt_file="/tmp/session/runtime/blackbeard/prompt.txt",
        )
        self.assertFalse(exceeds_windows_cmdline_limit(argv))
        self.assertLess(cmdline_length(argv), 2_000)
        self.assertNotIn(big_prompt, argv)

        file_idxs = [i for i, a in enumerate(argv) if a == "--file"]
        msg_idx = argv.index(PROMPT_FILE_MESSAGE)
        self.assertLess(msg_idx, file_idxs[0])
        self.assertEqual(argv[file_idxs[0] + 1], "/tmp/session/runtime/blackbeard/prompt.txt")
        self.assertIn("/tmp/session/packet.md", argv)

    def test_missing_prompt_file_falls_back_to_inline_message(self) -> None:
        from lib.runtime.opencode_adapter import _run_opencode_once

        captured: list[list[str]] = []

        class _Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def _runner(args, **_kw):
            captured.append(args)
            return _Proc()

        with tempfile.TemporaryDirectory() as td:
            packet = Path(td) / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            _run_opencode_once(
                invocation={"seat": "blackbeard", "packet_path": str(packet)},
                model="opencode-go/deepseek-v4-flash",
                prompt="INLINE PROMPT",
                workdir=Path(td),
                timeout_sec=30,
                env={},
                runner=_runner,
                prompt_file=Path(td) / "does-not-exist.txt",
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("INLINE PROMPT", captured[0])

    def test_permission_allows_external_session(self) -> None:
        from lib.runtime.opencode_adapter import build_opencode_permission_json

        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "repo"
            session = Path(td) / "session"
            work.mkdir()
            session.mkdir()
            packet = session / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            perm = json.loads(
                build_opencode_permission_json(
                    workdir=work,
                    session_dir=session,
                    packet_path=packet,
                    schema_path=session / "schema.json",
                )
            )
            self.assertEqual(perm.get("edit"), "deny")
            self.assertEqual(perm.get("bash"), "deny")
            self.assertIn("external_directory", perm)
            external = perm["external_directory"]
            self.assertIsInstance(external, dict)
            self.assertTrue(any(str(session.resolve()) in k for k in external))

    def test_invoke_passes_permission_and_arg_order(self) -> None:
        """OpenCode must receive exactly the prompt_builder prompt (not an obsolete argv prefix).

        Supply mechanism today: positional CLI message before --file (not stdin).
        Artefacts: runtime/<seat>/prompt.txt + prompt.meta.json must match that message.
        """
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "session"
            repo = Path(td) / "repo"
            session.mkdir()
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            (session / "session.json").write_text(
                json.dumps({"session_id": "oc-args", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            packet = session / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            out = session / "runtime" / "blackbeard" / "findings.json"
            schema_path = SKILL / "contracts" / "finding.schema.json"
            inv = {
                "schema_version": 1,
                "session_id": "oc-args",
                "session_dir": str(session),
                "review_type": "implementation",
                "seat": "blackbeard",
                "runtime": "opencode",
                "model": "opencode/deepseek-v4-flash",
                "packet_path": str(packet),
                "packet_hash": "h",
                "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
                "schema_path": str(schema_path),
                "output_path": str(out),
                "timeout_sec": 30,
                "permissions": {"read": True, "write": False},
                "workdir": str(repo),
                "runtime_options": {},
            }
            seen: dict[str, Any] = {}

            def capture(args, **kwargs):
                seen["args"] = list(args)
                seen["env"] = kwargs.get("env") or {}
                payload = {
                    "findings": [],
                    "notes": [],
                    "repos_reviewed": ["x"],
                    "attack_card": "n/a",
                    "disposition": "Content",
                }
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    with mock.patch(
                        "lib.runtime.opencode_adapter.run_opencode",
                        side_effect=lambda args, **kw: capture(args, **kw),
                    ):
                        from lib.runtime.opencode_adapter import invoke_opencode_seat
                        from lib.runtime.prompt_builder import build_reviewer_prompt

                        expected = build_reviewer_prompt(
                            packet_path=packet,
                            packet_hash="h",
                            schema_path=schema_path,
                            seat="blackbeard",
                            review_type="implementation",
                            attempt=1,
                        )
                        invoke_opencode_seat(inv)

            self.assertIn("args", seen, "OpenCode invocation did not occur")
            args = seen["args"]
            out_dir = session / "runtime" / "blackbeard"
            prompt_txt = out_dir / "prompt.txt"
            prompt_meta = out_dir / "prompt.meta.json"
            self.assertTrue(prompt_txt.is_file(), "prompt.txt missing")
            self.assertTrue(prompt_meta.is_file(), "prompt.meta.json missing")

            written = prompt_txt.read_text(encoding="utf-8")
            meta = json.loads(prompt_meta.read_text(encoding="utf-8"))
            self.assertEqual(written, expected["prompt"])
            self.assertEqual(meta["sharedPrefixHash"], expected["shared_prefix_hash"])
            self.assertEqual(meta["fullPromptHash"], expected["full_prompt_hash"])

            # Ordering: protocol → packet → schema → seat (in prompt body, not argv index).
            for earlier, later in (
                ("=== SECTION protocol ===", "=== SECTION packet"),
                ("=== SECTION packet", "=== SECTION finding_schema ==="),
                ("=== SECTION finding_schema ===", "=== SECTION seat ==="),
                ("=== SECTION seat ===", "=== SECTION run ==="),
            ):
                self.assertLess(written.index(earlier), written.index(later))

            # Supply mechanism: prompt travels as an attachment, never as argv.
            # Windows CreateProcess caps command lines at 32767 characters.
            from lib.runtime.opencode_adapter import (
                PROMPT_FILE_MESSAGE,
                exceeds_windows_cmdline_limit,
            )

            self.assertNotIn(written, args)
            self.assertFalse(exceeds_windows_cmdline_limit(args))

            prompt_file_idxs = [i for i, a in enumerate(args) if a == str(prompt_txt)]
            self.assertEqual(
                len(prompt_file_idxs),
                1,
                f"expected exactly one attached prompt.txt; got {len(prompt_file_idxs)}; args={args!r}",
            )

            # Packet (and schema) still attached as --file for tools; message must precede.
            file_idxs = [i for i, a in enumerate(args) if a == "--file"]
            self.assertGreaterEqual(len(file_idxs), 2)
            self.assertLess(args.index(PROMPT_FILE_MESSAGE), min(file_idxs))
            self.assertIn(str(packet), args)

            perm = json.loads(seen["env"]["OPENCODE_PERMISSION"])
            self.assertEqual(perm["edit"], "deny")
            self.assertIn("external_directory", perm)

    def test_extract_fenced_json(self) -> None:
        text = 'prefix\n```json\n{"findings":[]}\n```\n'
        obj = extract_json_candidate(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["findings"], [])

    def test_extract_findings_from_opencode_ndjson_part_text(self) -> None:
        fixture = SCRIPTS / "fixtures" / "runtime" / "opencode-ndjson-findings.stdout.txt"
        raw = fixture.read_text(encoding="utf-8")
        # Whole-buffer extract must not latch onto tool_use noise
        self.assertIsNone(extract_json_candidate(raw))
        obj = extract_findings_from_opencode_stdout(raw)
        self.assertIsNotNone(obj)
        self.assertIsInstance(obj.get("findings"), list)
        self.assertGreaterEqual(len(obj["findings"]), 1)

        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "session.json").write_text(
                json.dumps({"session_id": "oc-ndjson", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            packet = session / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            out = session / "runtime" / "blackbeard" / "findings.json"
            inv = {
                "schema_version": 1,
                "session_id": "oc-ndjson",
                "session_dir": str(session),
                "review_type": "implementation",
                "seat": "blackbeard",
                "runtime": "opencode",
                "model": "opencode/deepseek-v4-flash",
                "packet_path": str(packet),
                "packet_hash": "h",
                "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
                "schema_path": str(SKILL / "contracts" / "finding.schema.json"),
                "output_path": str(out),
                "timeout_sec": 30,
                "permissions": {"read": True, "write": False},
                "workdir": str(session),
                "runtime_options": {},
            }

            def ndjson_run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout=raw, stderr="")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    result = invoke_opencode_seat(inv, run_fn=ndjson_run)
            self.assertIsNone(result.get("failure_category"))
            self.assertTrue(result["completed"])
            self.assertTrue(result["schema_valid"])
            self.assertTrue(out.is_file())
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(saved.get("findings"), list)
            usage = result.get("usage") or {}
            self.assertEqual(usage.get("source"), "opencode_step_finish")
            self.assertGreaterEqual(int(usage.get("steps") or 0), 1)
            self.assertIsInstance(usage.get("cost"), (int, float))

    def test_extract_usage_from_opencode_step_finish(self) -> None:
        fixture = SCRIPTS / "fixtures" / "runtime" / "opencode-ndjson-findings.stdout.txt"
        usage = extract_usage_from_opencode_stdout(fixture.read_text(encoding="utf-8"))
        self.assertIsNotNone(usage)
        self.assertEqual(usage["source"], "opencode_step_finish")
        self.assertGreaterEqual(usage["steps"], 1)
        self.assertIsInstance(usage["cost"], float)
        self.assertGreater(usage["tokens"]["total"], 0)

    def test_redact_secrets(self) -> None:
        self.assertIn("***", redact_secrets("api_key=sk-abcdefghijklmnop"))

    def test_fake_missing_auth(self) -> None:
        env = os.environ.copy()
        env["YONKO_OPENCODE_BIN"] = str(FAKE_OC)
        env["YONKO_FAKE_OPENCODE_AUTH"] = "missing"
        with mock.patch.dict(os.environ, env, clear=False):
            from lib.runtime import opencode_adapter as oc

            ok, msg = oc.check_auth()
            self.assertFalse(ok)
            self.assertIn("authentication", msg.lower())

    def test_fake_models_and_match(self) -> None:
        env = os.environ.copy()
        env["YONKO_OPENCODE_BIN"] = str(FAKE_OC)
        with mock.patch.dict(os.environ, env, clear=False):
            from lib.runtime import opencode_adapter as oc

            models = oc.list_models()
            self.assertIn("opencode-go/deepseek-v4-pro", models)
            mid = rp.match_opencode_model(
                {"model": {"configured": "opencode-go/deepseek-v4-pro"}},
                models,
            )
            self.assertEqual(mid, "opencode-go/deepseek-v4-pro")
            with self.assertRaises(rp.ProfileError) as ctx:
                rp.match_opencode_model(
                    {"model": {"match_substrings": ["deepseek", "v4"]}},
                    models,
                )
            self.assertEqual(ctx.exception.category, "model_unavailable")
            self.assertIn("ambiguous", ctx.exception.message)

    def test_successful_json_and_timeout_categories(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            (session / "session.json").write_text(
                json.dumps({"session_id": "oc1", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            packet = session / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            out = session / "runtime" / "blackbeard" / "findings.json"
            inv = {
                "schema_version": 1,
                "session_id": "oc1",
                "session_dir": str(session),
                "review_type": "implementation",
                "seat": "blackbeard",
                "runtime": "opencode",
                "model": "opencode/deepseek-v4-flash",
                "packet_path": str(packet),
                "packet_hash": "h",
                "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
                "schema_path": str(SKILL / "contracts" / "finding.schema.json"),
                "output_path": str(out),
                "timeout_sec": 30,
                "permissions": {"read": True, "write": False},
                "workdir": str(session),
                "runtime_options": {},
            }

            def ok_run(args, **kwargs):
                payload = {"findings": [], "notes": [], "disposition": "Content"}
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(payload) + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    result = invoke_opencode_seat(inv, run_fn=ok_run)
            self.assertTrue(result["completed"] or result["schema_valid"])

            # Fresh output path so prior success cannot mask malformed stdout
            out2 = session / "runtime" / "blackbeard" / "findings2.json"
            inv2 = dict(inv)
            inv2["output_path"] = str(out2)

            def bad_run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout="not-json", stderr="")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    result2 = invoke_opencode_seat(inv2, run_fn=bad_run)
            self.assertEqual(result2["failure_category"], "malformed_output")

            inv3 = dict(inv)
            inv3["output_path"] = str(session / "runtime" / "blackbeard" / "findings3.json")

            def rate_run(args, **kwargs):
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="rate limit 429")

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    result3 = invoke_opencode_seat(inv3, run_fn=rate_run)
            self.assertIn(result3["failure_category"], ("rate_limited", "malformed_output", "process_failure"))

    def test_repo_modification_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "session"
            session.mkdir()
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            # Pre-existing dirt must NOT fail the seat
            (repo / "preexisting.txt").write_text("baseline\n", encoding="utf-8")
            (session / "session.json").write_text(
                json.dumps({"session_id": "oc2", "review_type": "implementation"}) + "\n",
                encoding="utf-8",
            )
            packet = session / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            out = session / "runtime" / "buggy" / "findings.json"
            inv = {
                "schema_version": 1,
                "session_id": "oc2",
                "session_dir": str(session),
                "review_type": "implementation",
                "seat": "buggy",
                "runtime": "opencode",
                "model": "opencode/qwen3.7-plus",
                "packet_path": str(packet),
                "packet_hash": "h",
                "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
                "schema_path": str(SKILL / "contracts" / "finding.schema.json"),
                "output_path": str(out),
                "timeout_sec": 30,
                "permissions": {"read": True, "write": False},
                "workdir": str(repo),
                "runtime_options": {},
            }

            def noop_run(args, **kwargs):
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout='{"findings":[],"disposition":"Content","attack_card":"n/a","notes":[],"repos_reviewed":["x"]}',
                    stderr="",
                )

            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    ok_result = invoke_opencode_seat(inv, run_fn=noop_run)
            self.assertNotEqual(ok_result.get("failure_category"), "permission_violation")
            self.assertTrue(ok_result.get("completed") or ok_result.get("schema_valid"))

            def dirty_run(args, **kwargs):
                (repo / "src_touched.txt").write_text("x\n", encoding="utf-8")
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout='{"findings":[],"disposition":"Content","attack_card":"n/a","notes":[],"repos_reviewed":["x"]}',
                    stderr="",
                )
            with mock.patch("lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")):
                with mock.patch("lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")):
                    result = invoke_opencode_seat(inv, run_fn=dirty_run)
            self.assertEqual(result["failure_category"], "permission_violation")
            self.assertTrue((repo / "src_touched.txt").is_file())  # not discarded

    def test_further_edit_to_already_dirty_file_fails(self) -> None:
        from lib.runtime.opencode_adapter import snapshot_workdir, worktree_delta, allowed_write_prefixes

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            session = Path(td) / "session"
            repo.mkdir()
            session.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            tracked = repo / "App.java"
            tracked.write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "add", "App.java"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            tracked.write_text("v2-user\n", encoding="utf-8")  # pre-existing dirt
            before = snapshot_workdir(repo)
            tracked.write_text("v3-opencode\n", encoding="utf-8")  # further edit
            after = snapshot_workdir(repo)
            illicit = worktree_delta(
                before, after, allowed_prefixes=allowed_write_prefixes(repo, session)
            )
            self.assertIn("App.java", illicit)

    def test_baseline_dirty_unchanged_is_ok(self) -> None:
        from lib.runtime.opencode_adapter import snapshot_workdir, worktree_delta, allowed_write_prefixes

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            session = Path(td) / "session"
            repo.mkdir()
            session.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            (repo / "notes.md").write_text("user wip\n", encoding="utf-8")
            before = snapshot_workdir(repo)
            after = snapshot_workdir(repo)
            illicit = worktree_delta(
                before, after, allowed_prefixes=allowed_write_prefixes(repo, session)
            )
            self.assertEqual(illicit, [])

    def test_findings_envelope_requires_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "findings.json"
            path.write_text(
                json.dumps(
                    {
                        "findings": [
                            {
                                "id": "B1",
                                "reviewer": "blackbeard",
                                "category": "correctness",
                                "severity": "critical",
                                "title": "x",
                                "claim": "y",
                                "locus": {"repository": "r", "path": "a.java"},
                                "evidence": "diff hunk",
                                "reachability": "call path",
                                "impact": "breaks",
                                "confidence": "high",
                            }
                        ],
                        "attack_card": "n/a",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            bad = subprocess.run(
                [
                    "bash",
                    str(SCRIPTS / "validate-artifact.sh"),
                    "--kind",
                    "findings",
                    "--file",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("disposition", bad.stdout + bad.stderr)

            data = json.loads(path.read_text())
            data["disposition"] = "Remand"
            path.write_text(json.dumps(data) + "\n", encoding="utf-8")
            good = subprocess.run(
                [
                    "bash",
                    str(SCRIPTS / "validate-artifact.sh"),
                    "--kind",
                    "findings",
                    "--file",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)


class CursorDurationTests(unittest.TestCase):
    def test_record_cursor_completion_sets_duration_ms(self) -> None:
        from lib.runtime.cursor_adapter import invoke_cursor_seat, record_cursor_completion
        import time

        with tempfile.TemporaryDirectory() as td:
            session = Path(td)
            packet = session / "packet.md"
            packet.write_text("p\n", encoding="utf-8")
            schema = SKILL / "contracts" / "finding.schema.json"
            inv = {
                "seat": "shanks",
                "model": "grok",
                "model_configured": "grok",
                "model_resolved": "grok",
                "session_dir": str(session),
                "packet_path": str(packet),
                "packet_hash": "h",
                "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
                "schema_path": str(schema),
                "output_path": str(session / "runtime" / "shanks" / "findings.json"),
                "review_type": "implementation",
            }
            first = invoke_cursor_seat(inv)
            self.assertTrue(first["awaiting_chair_dispatch"])
            self.assertEqual(first["duration_ms"], 0)
            time.sleep(1.05)
            (session / "runtime" / "shanks" / "findings.json").write_text(
                json.dumps(
                    {
                        "findings": [],
                        "notes": [],
                        "disposition": "Content",
                        "attack_card": "n/a",
                        "repos_reviewed": ["x"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            done = record_cursor_completion(
                session,
                "shanks",
                model_actual="grok",
                output_path=str(session / "runtime" / "shanks" / "findings.json"),
                schema_valid=True,
                completed=True,
            )
            self.assertFalse(done["awaiting_chair_dispatch"])
            self.assertTrue(done["completed"])
            self.assertGreaterEqual(done["duration_ms"] or 0, 1000)
            self.assertIsNotNone(done["started_at"])
            self.assertIsNotNone(done["ended_at"])


class DoctorTests(unittest.TestCase):
    def test_cursor_standard_skips_opencode(self) -> None:
        report = doctor(profile_id="cursor-standard")
        self.assertEqual(report["executionProfile"], "cursor-standard")
        names = [c["name"] for c in report["checks"]]
        self.assertIn("OpenCode checks skipped", names)
        self.assertTrue(report["ok"])

    def test_machine_readable(self) -> None:
        report = doctor(profile_id="cursor-max")
        self.assertIn("exit_code", report)
        self.assertIn("checks", report)

    def test_opencode_profile_reports_missing_cli(self) -> None:
        env = os.environ.copy()
        env["YONKO_OPENCODE_BIN"] = "/nonexistent/opencode-binary"
        # clear PATH lookup by forcing override to missing path
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("lib.runtime.opencode_adapter.resolve_opencode_bin", return_value=None):
                report = doctor(profile_id="cursor-opencode-go")
        self.assertFalse(report["ok"])
        failed = [c for c in report["checks"] if not c["ok"]]
        self.assertTrue(any("OpenCode CLI" in c["name"] for c in failed))


class InitSessionFreezeTests(unittest.TestCase):
    def test_init_session_freezes_profile(self) -> None:
        env = os.environ.copy()
        with tempfile.TemporaryDirectory() as td:
            env["YONKO_SESSIONS_ROOT"] = td
            marker = SKILL / "config" / "execution-profile.json"
            previous = marker.read_text(encoding="utf-8") if marker.is_file() else None
            try:
                marker.write_text(
                    json.dumps({"schema_version": 1, "executionProfile": "cursor-standard"}) + "\n",
                    encoding="utf-8",
                )
                proc = _run(
                    [str(SCRIPTS / "init-session.sh"), "--id", "ep-freeze-1", "--type", "implementation"],
                    env=env,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                session = Path(proc.stdout.strip())
                data = json.loads((session / "session.json").read_text(encoding="utf-8"))
                self.assertTrue(data.get("execution_profile", {}).get("frozen"))
                self.assertEqual(data["execution_profile"]["executionProfile"], "cursor-standard")
            finally:
                if previous is None:
                    marker.unlink(missing_ok=True)
                else:
                    marker.write_text(previous, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
