#!/usr/bin/env python3
"""Reliability smokes for packet_plus_workspace_read seats.

Pins frozen repair, live seat visibility, and coach nudges (session continue)
instead of hard-cutting slow finishers.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from lib.runtime.opencode_adapter import (  # noqa: E402
    EXPLORATION_GRACE_SECONDS,
    ExplorationWatchdog,
    _build_prompt,
    _exploration_hard_timeout,
    _exploration_tool_call_cap,
    build_opencode_permission_json,
    build_opencode_run_args,
    coach_nudge_message,
    invoke_opencode_seat,
)

BUDGET = {
    "max_files_read": 40,
    "max_searches": 25,
    "max_lsp_queries": 15,
    "max_extra_bytes": 400000,
    "max_duration_seconds": 240,
}
PAYLOAD = {"findings": [], "notes": [], "disposition": "Content"}


def make_invocation(session: Path, name: str) -> dict:
    (session / "session.json").write_text(
        json.dumps({"session_id": "rel1", "review_type": "implementation"}) + "\n",
        encoding="utf-8",
    )
    packet = session / "packet.md"
    packet.write_text("packet\n", encoding="utf-8")
    return {
        "schema_version": 1,
        "session_id": "rel1",
        "session_dir": str(session),
        "review_type": "implementation",
        "seat": "blackbeard",
        "runtime": "opencode",
        "model": "opencode/deepseek-v4-flash",
        "packet_path": str(packet),
        "packet_hash": "h",
        "prompt_path": str(SKILL / "prompts" / "reviewers.md"),
        "schema_path": str(SKILL / "contracts" / "finding.schema.json"),
        "output_path": str(session / "runtime" / name / "findings.json"),
        "timeout_sec": 300,
        "permissions": {"read": True, "write": False},
        "workdir": str(session),
        "workspace_root": str(session),
        "review_mode": "packet_plus_workspace_read",
        "exploration_budget": dict(BUDGET),
        "runtime_options": {},
    }


def run_seat(inv: dict, run_fn) -> dict:
    with mock.patch(
        "lib.runtime.opencode_adapter.check_installed", return_value=(True, "fake")
    ):
        with mock.patch(
            "lib.runtime.opencode_adapter.check_auth", return_value=(True, "ok")
        ):
            return invoke_opencode_seat(inv, run_fn=run_fn)


def test_convergence_law_in_prompt(session: Path) -> None:
    inv = make_invocation(session, "prompt")
    prompt = _build_prompt(inv)["prompt"]
    assert "seat coach watches your" in prompt
    assert "re-prompted" in prompt
    assert "slow finishers are not interrupted" in prompt
    assert perm_bash_denied(session)


def perm_bash_denied(session: Path) -> bool:
    perm = json.loads(
        build_opencode_permission_json(
            workdir=session,
            session_dir=session,
            packet_path=session / "packet.md",
            schema_path=SKILL / "contracts/finding.schema.json",
            prompt_path=SKILL / "prompts/reviewers.md",
            review_mode="packet_plus_workspace_read",
            workspace_root=session,
        )
    )
    assert perm["bash"] == "deny"
    return True


def test_tool_only_turn_repairs_frozen(session: Path) -> None:
    inv = make_invocation(session, "toolonly")
    out = Path(inv["output_path"])
    calls: list[dict] = []

    def run(args, **kwargs):
        calls.append(json.loads((kwargs.get("env") or {})["OPENCODE_PERMISSION"]))
        if len(calls) == 1:
            ndjson = "\n".join(
                json.dumps({"type": "tool_use", "part": {"tool": t}})
                for t in ("read", "grep", "read", "glob")
            )
            return subprocess.CompletedProcess(args, 0, stdout=ndjson, stderr="")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(PAYLOAD) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(PAYLOAD), stderr=""
        )

    result = run_seat(inv, run)
    assert len(calls) == 2
    assert calls[0]["grep"] == "allow"
    for tool in ("read", "glob", "grep", "list", "lsp"):
        assert calls[1][tool] == "deny"
    assert result["completed"] is True, result.get("failure_message")


def test_coach_nudge_continues_session(session: Path) -> None:
    inv = make_invocation(session, "coach")
    inv["timeout_sec"] = 600
    out = Path(inv["output_path"])
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        wd = kwargs.get("watchdog")
        if len(calls) == 1:
            assert wd is not None
            # Simulate stream having set a session id before the coach interrupt.
            wd.session_id = "ses_test_coach_1"
            wd.cut_reason = "nudge:no_progress"
            raise subprocess.TimeoutExpired(
                args, 300, stderr="watchdog:nudge:no_progress"
            )
        # Second call must continue the OpenCode session with a coach message.
        assert "--session" in args
        assert "ses_test_coach_1" in args
        assert any("COACH NUDGE" in str(a) for a in args)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(PAYLOAD) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(PAYLOAD), stderr=""
        )

    result = run_seat(inv, run)
    assert len(calls) == 2
    assert result["completed"] is True, result.get("failure_message")
    status = session / "runtime" / "blackbeard" / "seat-status.json"
    assert status.is_file(), "live seat-status.json must exist for visibility"
    doc = json.loads(status.read_text())
    assert doc["nudges"] >= 1
    assert doc["session_id"] == "ses_test_coach_1"


def test_watchdog_nudge_vs_finisher() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wander = ExplorationWatchdog(
            soft_sec=240,
            absolute_sec=600,
            grace_sec=60,
            tool_call_cap=24,
            status_path=root / "seat-status.json",
            progress_path=root / "progress.jsonl",
        )
        for _ in range(10):
            wander.observe_line(
                json.dumps(
                    {
                        "type": "tool_use",
                        "sessionID": "ses_abc",
                        "part": {"tool": "read"},
                    }
                )
            )
        assert wander.session_id == "ses_abc"
        assert wander.decision(100) is None
        assert wander.decision(300) == "nudge:no_progress"
        assert (root / "seat-status.json").is_file()
        assert (root / "progress.jsonl").is_file()

        finisher = ExplorationWatchdog(
            soft_sec=240, absolute_sec=600, grace_sec=60, tool_call_cap=24
        )
        finisher.observe_line(
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_fin",
                    "part": {
                        "type": "text",
                        "text": json.dumps(PAYLOAD),
                    },
                }
            )
        )
        assert finisher.saw_finishing is True
        assert finisher.decision(300) is None
        assert finisher.decision(500) is None
        assert finisher.decision(600) == "cut:absolute_timeout"


def test_session_continue_args() -> None:
    args = build_opencode_run_args(
        model="m",
        title="t",
        prompt="p",
        packet_path="/tmp/packet.md",
        session_id="ses_1",
        message=coach_nudge_message("no_progress"),
        extra_files=["/tmp/schema.json"],
    )
    assert "--session" in args and "ses_1" in args
    assert "/tmp/packet.md" not in args
    assert "/tmp/schema.json" in args
    assert any("COACH NUDGE" in a for a in args)


def test_explore_helpers() -> None:
    assert EXPLORATION_GRACE_SECONDS == 60
    assert _exploration_hard_timeout(600, {"max_duration_seconds": 240}) == 300
    assert _exploration_tool_call_cap(BUDGET) == 24


def test_repair_failure_still_reports(session: Path) -> None:
    inv = make_invocation(session, "hopeless")
    calls: list[str] = []

    def run(args, **kwargs):
        calls.append("x")
        return subprocess.CompletedProcess(args, 0, stdout="no json here", stderr="")

    result = run_seat(inv, run)
    assert len(calls) == 2
    assert result["completed"] is False
    assert result["failure_category"] == "malformed_output"


def main() -> None:
    for fn in (
        test_convergence_law_in_prompt,
        test_tool_only_turn_repairs_frozen,
        test_coach_nudge_continues_session,
        test_watchdog_nudge_vs_finisher,
        test_session_continue_args,
        test_explore_helpers,
        test_repair_failure_still_reports,
    ):
        if fn.__name__ in (
            "test_watchdog_nudge_vs_finisher",
            "test_session_continue_args",
            "test_explore_helpers",
        ):
            fn()
        else:
            with tempfile.TemporaryDirectory() as td:
                fn(Path(td))
        print(f"  ok {fn.__name__}")
    print("All exploration reliability smokes passed.")


if __name__ == "__main__":
    main()
