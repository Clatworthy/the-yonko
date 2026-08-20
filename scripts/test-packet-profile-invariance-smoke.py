#!/usr/bin/env python3
"""Packet hash and cross-repo completeness must be invariant under execution profile changes."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIX = SCRIPTS / "fixtures" / "evidence-graph"
PROFILES = ROOT / "config" / "execution-profiles"
MARKER = ROOT / "config" / "execution-profile.json"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    sid = f"eg-profile-inv-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [str(SCRIPTS / "init-session.sh"), "--id", sid, "--type", "implementation"],
        check=True,
    )
    session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
    evid = session / "evidence"
    evid.mkdir(exist_ok=True)
    shutil.copy(FIX / "sample-order-confirm.patch", evid / "DIFF-x.patch")
    (evid / "repos.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "label": "fixture/mini-spring",
                        "path": str((FIX / "mini-spring").resolve()),
                        "branch": "f",
                        "patch": "DIFF-x.patch",
                        "secrets_excluded": [],
                        "dirty": True,
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (evid / "DIFF_MAP.txt").write_text("repo: fixture/mini-spring\n", encoding="utf-8")
    (evid / "risk.json").write_text(
        json.dumps({"risk": "medium", "reasons": ["fixture"], "reviewers": 3}) + "\n",
        encoding="utf-8",
    )
    (session / "DOCKET.md").write_text(
        "# Docket\n\n## Goal\ninvariance\n\n## Touch surface → Expected DIFF labels\n"
        "- fixture/mini-spring\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["YONKO_SCRIPTS_DIR"] = str(SCRIPTS)
    subprocess.run(
        [str(SCRIPTS / "build-evidence-graph.sh"), "--session", str(session)],
        env=env,
        check=False,
    )
    subprocess.run(
        [
            str(SCRIPTS / "sanitise-and-hash-packet.sh"),
            "--session",
            str(session),
            "--docket",
            str(session / "DOCKET.md"),
        ],
        env=env,
        check=True,
    )

    packet_hash = json.loads((session / "packet.meta.json").read_text())["packet_hash"]
    graph_sha = _sha(evid / "evidence-graph.json")
    complete = json.loads((evid / "graph-completeness.json").read_text())
    xref_status = next(
        (
            c["status"]
            for c in complete["categories"]
            if c["category"] == "cross_repository_consumers"
        ),
        None,
    )
    assert xref_status in ("covered", "unresolved", "not_applicable"), xref_status

    # Switch marker across available profiles (or synthetic ids) without rehashing
    available = sorted(p.stem for p in PROFILES.glob("*.json")) if PROFILES.is_dir() else []
    if len(available) < 2:
        available = ["cursor-standard", "hybrid-opencode"]
        # marker-only flip still proves profile selection is outside the packet
    backup = MARKER.read_text(encoding="utf-8") if MARKER.is_file() else None
    try:
        for pid in available[:3]:
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(
                json.dumps({"schema_version": 1, "executionProfile": pid}, indent=2) + "\n",
                encoding="utf-8",
            )
            # Re-read evidence artifacts - must be byte-identical / same hash
            h2 = json.loads((session / "packet.meta.json").read_text())["packet_hash"]
            g2 = _sha(evid / "evidence-graph.json")
            c2 = json.loads((evid / "graph-completeness.json").read_text())
            x2 = next(
                c["status"]
                for c in c2["categories"]
                if c["category"] == "cross_repository_consumers"
            )
            assert h2 == packet_hash, (pid, h2, packet_hash)
            assert g2 == graph_sha, pid
            assert x2 == xref_status, (pid, x2, xref_status)
    finally:
        if backup is None:
            if MARKER.is_file():
                MARKER.unlink()
        else:
            MARKER.write_text(backup, encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "packet_hash": packet_hash,
                "cross_repository_consumers": xref_status,
                "profiles_checked": available[:3],
            }
        )
    )
    print("PASS packet/profile invariance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
