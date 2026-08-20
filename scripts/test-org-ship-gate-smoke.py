#!/usr/bin/env python3
"""Smoke: org ship gate fail-closed validate + finalize codes."""
from __future__ import annotations

import json
import sys
import tempfile
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(rel: str, name: str):
    path = SCRIPTS / rel
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_session(tmp: Path) -> Path:
    sid = f"org-gate-{uuid.uuid4().hex[:8]}"
    session = tmp / sid
    session.mkdir()
    (session / "session.json").write_text(
        json.dumps(
            {
                "session_id": sid,
                "review_type": "implementation",
                "status": "active",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (session / "adapter.json").write_text(
        json.dumps({"org_ship_gate": {"enabled": True}}) + "\n",
        encoding="utf-8",
    )
    return session


def test_missing_result_fails() -> None:
    sbg = load("lib/org_ship_gate.py", "org_ship_gate_smoke")
    with tempfile.TemporaryDirectory() as td:
        session = make_session(Path(td))
        result = sbg.validate_session_gate(session)
        assert result["ok"] is False
        assert result["code"] == "ORG_SHIP_GATE_REQUIRED"
    print("PASS missing result → ORG_SHIP_GATE_REQUIRED")


def test_empty_findings_without_attack_fails() -> None:
    sbg = load("lib/org_ship_gate.py", "org_ship_gate_smoke2")
    with tempfile.TemporaryDirectory() as td:
        session = make_session(Path(td))
        gate = session / "org-ship-gate"
        gate.mkdir()
        (gate / "result.json").write_text(
            json.dumps(
                {
                    "verdict": "pass",
                    "disposition": "Content",
                    "findings": [],
                    "attack_card": "short",
                    "one_sentence_bot_would_break": "x",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = sbg.validate_session_gate(session)
        assert result["ok"] is False
        assert result["code"] == "ORG_SHIP_GATE_FAILED"
    print("PASS short attack card → ORG_SHIP_GATE_FAILED")


def test_hostile_pass_ok() -> None:
    sbg = load("lib/org_ship_gate.py", "org_ship_gate_smoke3")
    attack = (
        "Attack card:\n"
        "- Golden path compared to: create path\n"
        "- Identity sources in diff: resource customer; diverge null principal tested\n"
        "- Reserved-key lifecycle: claim/mine/conflict/stale-repair/release/transfer; "
        "concurrent stale race + batch doomed-destination covered\n"
        "- Sibling / shared-parent case: n/a\n"
        "- Guarded delete vs irreversible side effects: n/a\n"
        "- Side-effect leaf opened: n/a\n"
        "- Tests added for adversary cases: divergePrincipal, concurrentStale\n"
    )
    with tempfile.TemporaryDirectory() as td:
        session = make_session(Path(td))
        gate = session / "org-ship-gate"
        gate.mkdir()
        (gate / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "verdict": "pass",
                    "disposition": "Content",
                    "posture": "hostile",
                    "model": "opencode-go/gpt-5.6-luna",
                    "findings": [],
                    "attack_card": attack,
                    "one_sentence_bot_would_break": (
                        "Admin principal with null customer id claiming a reserved key "
                        "owned by a live resource-scoped entity."
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = sbg.validate_session_gate(session)
        assert result["ok"] is True, result
    print("PASS hostile empty findings + Attack card")


def test_nonempty_findings_fail() -> None:
    sbg = load("lib/org_ship_gate.py", "org_ship_gate_smoke4")
    attack = "Attack card:\n- Golden path compared to: x\n" + ("y" * 100)
    with tempfile.TemporaryDirectory() as td:
        session = make_session(Path(td))
        gate = session / "org-ship-gate"
        gate.mkdir()
        (gate / "result.json").write_text(
            json.dumps(
                {
                    "verdict": "fail",
                    "disposition": "Remand",
                    "findings": [{"id": "Sb1", "severity": "high", "claim": "bug"}],
                    "attack_card": attack,
                    "one_sentence_bot_would_break": "the bug above",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = sbg.validate_session_gate(session)
        assert result["ok"] is False
        assert result["code"] == "ORG_SHIP_GATE_FAILED"
    print("PASS non-empty findings → ORG_SHIP_GATE_FAILED")


def test_script_exists() -> None:
    script = SCRIPTS / "run-org-ship-gate.sh"
    assert script.is_file(), script
    text = script.read_text(encoding="utf-8")
    assert "opencode-go/gpt-5.6-luna" in text
    assert "run_org_ship_gate_opencode.py" in text
    assert "codex" not in text.lower()
    print("PASS run-org-ship-gate.sh present (OpenCode Go)")


def test_skill_mentions_15b() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "15b" in skill
    assert "run-org-ship-gate.sh" in skill
    assert "ORG_SHIP_GATE_REQUIRED" in skill
    print("PASS SKILL.md step 15b")


def test_failure_codes_registered() -> None:
    state = load("workflow/state.py", "yonko_state_sbg")
    assert "ORG_SHIP_GATE_REQUIRED" in state.FAILURE_CODES
    assert "ORG_SHIP_GATE_FAILED" in state.FAILURE_CODES
    print("PASS FAILURE_CODES registered")


def main() -> int:
    test_missing_result_fails()
    test_empty_findings_without_attack_fails()
    test_hostile_pass_ok()
    test_nonempty_findings_fail()
    test_script_exists()
    test_skill_mentions_15b()
    test_failure_codes_registered()
    print("All org-ship-gate smokes passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
