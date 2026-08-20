#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lib.runtime.invoke_seat import build_invocation
from lib.runtime.normalise_result import extract_repository_exploration
from lib.runtime.opencode_adapter import (
    _build_prompt,
    build_opencode_permission_json,
    build_opencode_run_args,
)
from lib.runtime.resolve_profile import freeze_profile_into_session, load_profile

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent


def boot() -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp())
    workspace = root / "workspace"
    repo = workspace / "services" / "demo"
    repo.mkdir(parents=True)
    session = root / "session"
    session.mkdir()
    (session / "session.json").write_text(
        json.dumps(
            {
                "session_id": "x",
                "review_type": "implementation",
                "packet_hash": "h",
            }
        )
        + "\n"
    )
    (session / "packet.md").write_text("packet\n")
    (session / "packet.meta.json").write_text(
        json.dumps({"packet_hash": "h"}) + "\n"
    )
    evid = session / "evidence"
    evid.mkdir()
    (evid / "repos.json").write_text(
        json.dumps(
            {
                "workspace_root": str(workspace),
                "repos": [{"label": "demo", "path": str(repo)}],
            }
        )
        + "\n"
    )
    (evid / "routing.json").write_text(
        json.dumps({"risk_band": "high", "seats": ["blackbeard"]}) + "\n"
    )
    freeze_profile_into_session(
        session, load_profile("cursor-opencode-go"), force=True
    )
    return session, workspace


def main() -> None:
    cfg = json.loads(
        (SKILL / "config/repository-exploration.json").read_text()
    )
    assert cfg["live_review_mode"] == "packet_plus_workspace_read"
    assert cfg["replay_modes"]["frozen_packet"] == "packet_only"

    session, workspace = boot()
    inv = build_invocation(session, "blackbeard")
    assert inv["review_mode"] == "packet_plus_workspace_read"
    assert Path(inv["workdir"]).resolve() == (
        workspace / "services" / "demo"
    ).resolve()
    assert Path(inv["workspace_root"]).resolve() == workspace.resolve()
    assert inv["exploration_budget"]["max_files_read"] >= 40
    prompt = _build_prompt(inv)["prompt"]
    assert "Packet is the authoritative starting evidence" in prompt
    assert "also cite the Packet locus" in prompt
    assert "Do not launch subagents" in prompt

    raw = build_opencode_permission_json(
        workdir=workspace,
        session_dir=session,
        packet_path=session / "packet.md",
        schema_path=SKILL / "contracts/finding.schema.json",
        prompt_path=SKILL / "prompts/reviewers.md",
        review_mode="packet_plus_workspace_read",
        workspace_root=workspace,
    )
    perm = json.loads(raw)
    sensitive_name = ".env"
    assert perm["read"]["*"] == "allow"
    assert perm["read"][f"**/{sensitive_name}"] == "deny"
    assert perm["grep"] == "allow"
    assert perm["lsp"] == "allow"
    assert perm["edit"] == "deny"
    assert perm["task"] == "deny"
    assert perm["webfetch"] == "deny"
    assert perm["bash"] == "deny"
    ext = perm["external_directory"]
    assert ext[f"**/{sensitive_name}"] == "deny"

    frozen = json.loads(
        build_opencode_permission_json(
            workdir=workspace,
            session_dir=session,
            packet_path=session / "packet.md",
            schema_path=SKILL / "contracts/finding.schema.json",
            prompt_path=SKILL / "prompts/reviewers.md",
            review_mode="packet_only",
            workspace_root=workspace,
        )
    )
    assert frozen["read"] == "deny"
    assert frozen["glob"] == "deny"
    assert frozen["grep"] == "deny"
    frozen_prompt = _build_prompt({**inv, "review_mode": "packet_only"})["prompt"]
    assert "do not call any tools" in frozen_prompt.lower()
    assert "findings JSON" in frozen_prompt

    args = build_opencode_run_args(
        model="m",
        title="t",
        prompt="p",
        packet_path=str(session / "packet.md"),
        workdir=str(workspace),
        agent="yonko-reviewer",
    )
    assert "--agent" in args and "yonko-reviewer" in args
    assert "--auto" not in args

    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "grep",
                        "state": {
                            "input": {
                                "pattern": "useCredits",
                                "path": str(workspace),
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "read",
                        "state": {
                            "input": {
                                "filePath": str(
                                    workspace
                                    / "services/demo/CreditService.java"
                                )
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "lsp",
                        "state": {
                            "input": {
                                "operation": "references",
                                "symbol": "useCredits",
                            }
                        },
                    },
                }
            ),
        ]
    )
    ledger = extract_repository_exploration(
        stdout,
        workspace_root=workspace,
        budget=inv["exploration_budget"],
    )
    assert ledger["enabled"] is True
    assert ledger["filesRead"][0]["path"].endswith("CreditService.java")
    assert ledger["searches"][0]["query"] == "useCredits"
    assert ledger["lspLookups"][0]["symbol"] == "useCredits"
    replay = (SKILL / "scripts/evals/replay-case.py").read_text()
    assert '"packet_only"' in replay
    assert '"packet_plus_workspace_read"' in replay
    finalize = (SKILL / "scripts/finalize-session.sh").read_text()
    assert "repository-exploration-summary.json" in finalize
    assert "packet_omission_candidate" in finalize
    print("All OpenCode workspace exploration smokes passed.")


if __name__ == "__main__":
    main()
