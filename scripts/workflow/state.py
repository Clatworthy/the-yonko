"""Yonko V3.4 workflow state - session artefact only (no runtime)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW_VERSION = "1.0.0"
EVENT_SCHEMA_VERSION = "1.0.0"

STATE_INVARIANTS: dict[str, str] = {
    "INIT": "session.json exists with review_type set",
    "EVIDENCE_READY": "type-correct evidence artefacts exist under evidence/",
    "RISK_SET": "evidence/risk.json or evidence/scope-risk.json exists",
    "PACKET_PINNED": "packet.md exists; session.packet_hash matches file; pin fingerprints stored",
    "SEATED": "reviewers_seated recorded; seat_count meets band minimum",
    "FINDINGS_VALID": "validate-artifact succeeded for findings kind",
    "VERIFIED": "verification_completed present when band requires verify",
    "APPLIED_OR_REVISED": "apply or artifact_revised recorded",
    "SCOPED_OK": "scoped_verify recorded (implementation)",
    "AWAITING_HUMAN": "plan/document awaiting human-approval.json",
    "FINALIZED": "finalize completed under active mode rules",
    "PUBLISHABLE": "evidence publish path observed",
}

# ADJUDICATED removed as a required legality state - no mechanical invariant without Chair call.
# Kept out of the active machine; disposition remains AI-owned.

FAILURE_CODES = frozenset({
    "PRECONDITION_FAILED",
    "PACKET_STALE",
    "PACKET_HASH_MISMATCH",
    "REVIEWER_INCOMPLETE",
    "OPENCODE_EXECUTE_MISSING",
    "ORG_SHIP_GATE_REQUIRED",
    "ORG_SHIP_GATE_FAILED",
    "VERIFICATION_REQUIRED",
    "OPEN_MATERIAL_FINDINGS",
    "HUMAN_APPROVAL_REQUIRED",
    "BUDGET_EXCEEDED",
    "PLAN_CONFIRMATION_REQUIRED",
    "WRITE_POLICY_VIOLATION",
    "ILLEGAL_TRANSITION",
})

TRANSITIONS: dict[str, dict[str, Any]] = {
    "initialise": {"to": "INIT", "from": {None, "INIT"}},
    "collect_evidence": {"to": "EVIDENCE_READY", "from": {"INIT", "EVIDENCE_READY", "RISK_SET", "PACKET_PINNED", "SEATED"}},
    "classify_risk": {"to": "RISK_SET", "from": {"EVIDENCE_READY", "RISK_SET", "PACKET_PINNED"}},
    "pin_packet": {"to": "PACKET_PINNED", "from": {"RISK_SET", "EVIDENCE_READY", "PACKET_PINNED", "SEATED", "FINDINGS_VALID"}},
    "invalidate_packet": {"to": "RISK_SET", "from": {"PACKET_PINNED", "SEATED", "FINDINGS_VALID", "VERIFIED", "APPLIED_OR_REVISED", "SCOPED_OK", "AWAITING_HUMAN", "EVIDENCE_READY"}},
    "seat_reviewers": {"to": "SEATED", "from": {"PACKET_PINNED", "SEATED"}},
    "validate_findings": {"to": "FINDINGS_VALID", "from": {"SEATED", "FINDINGS_VALID", "VERIFIED"}},
    "verify": {"to": "VERIFIED", "from": {"FINDINGS_VALID", "SEATED", "VERIFIED", "APPLIED_OR_REVISED"}},
    "apply_or_revise": {"to": "APPLIED_OR_REVISED", "from": {"VERIFIED", "FINDINGS_VALID", "SEATED", "APPLIED_OR_REVISED", "AWAITING_HUMAN"}},
    "scoped_verify": {"to": "SCOPED_OK", "from": {"APPLIED_OR_REVISED", "VERIFIED", "SCOPED_OK"}},
    "open_human_runway": {"to": "AWAITING_HUMAN", "from": {"APPLIED_OR_REVISED", "VERIFIED", "FINDINGS_VALID", "AWAITING_HUMAN"}},
    "human_approve_artifact": {"to": "AWAITING_HUMAN", "from": {"AWAITING_HUMAN", "APPLIED_OR_REVISED", "VERIFIED", "PACKET_PINNED", "SEATED", "FINDINGS_VALID"}},
    "finalize": {
        "to": "FINALIZED",
        "from": {
            "PACKET_PINNED", "SEATED", "FINDINGS_VALID", "VERIFIED",
            "APPLIED_OR_REVISED", "SCOPED_OK", "AWAITING_HUMAN", "FINALIZED",
        },
    },
    "publish_evidence": {"to": "PUBLISHABLE", "from": {"FINALIZED", "PUBLISHABLE"}},
    "rematch": {"to": "PACKET_PINNED", "from": {"SEATED", "FINDINGS_VALID", "VERIFIED", "APPLIED_OR_REVISED", "SCOPED_OK", "AWAITING_HUMAN"}},
    "human_override_legality": {"to": None, "from": None},  # does not change state
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def workflow_path(session_dir: Path) -> Path:
    return session_dir / "workflow.json"


def workflow_events_path(session_dir: Path) -> Path:
    return session_dir / "workflow-events.jsonl"


def lock_path(session_dir: Path) -> Path:
    return session_dir / "workflow.lock"


def approval_path(session_dir: Path) -> Path:
    return session_dir / "human-approval.json"


def override_path(session_dir: Path) -> Path:
    return session_dir / "human-override.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def load_session(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "session.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def fingerprint_paths(paths: list[Path]) -> str | None:
    parts: list[str] = []
    for p in sorted(paths, key=lambda x: str(x)):
        if p.is_file():
            parts.append(f"{p.name}:{sha256_bytes(p.read_bytes())}")
    if not parts:
        return None
    return sha256_text("\n".join(parts))


def docket_fingerprint(session_dir: Path) -> str | None:
    return fingerprint_paths([
        session_dir / "DOCKET.md",
        session_dir / "PLAN_DOCKET.md",
        session_dir / "DOC_DOCKET.md",
        session_dir / "docket.md",
        session_dir / "d.md",
        session_dir / "pd.md",
    ])


def evidence_fingerprint(session_dir: Path) -> str | None:
    evid = session_dir / "evidence"
    if not evid.is_dir():
        return None
    paths: list[Path] = []
    for name in (
        "repos.json", "plan-refs.json", "doc-refs.json", "plan.md", "document.md",
        "DIFF_MAP.txt", "recon.md", "approved-plan.md",
        "evidence-graph.json", "graph-completeness.json",
    ):
        paths.append(evid / name)
    repos = evid / "repos.json"
    if repos.exists():
        try:
            data = json.loads(repos.read_text(encoding="utf-8"))
            for r in data.get("repos") or []:
                patch = r.get("patch")
                if patch:
                    paths.append(evid / patch)
        except json.JSONDecodeError:
            pass
    return fingerprint_paths(paths)


def linked_plan_fingerprint(session_dir: Path) -> str | None:
    session = load_session(session_dir)
    linked = session.get("linked_session")
    if not linked:
        return None
    p = Path(linked) / "PLAN.approved.md"
    if not p.is_file():
        p2 = session_dir / "evidence" / "approved-plan.md"
        if p2.is_file():
            return fingerprint_paths([p2])
        return None
    return fingerprint_paths([p])


def default_state(session: dict[str, Any] | None = None, mode: str = "enforce") -> dict[str, Any]:
    session = session or {}
    return {
        "workflow_version": WORKFLOW_VERSION,
        "mode": mode,
        "review_type": session.get("review_type") or "implementation",
        "artifact_type": session.get("artifact_type"),
        "current_state": "INIT",
        "packet_hash": session.get("packet_hash"),
        "packet_stale": False,
        "docket_fingerprint": None,
        "evidence_fingerprint": None,
        "linked_plan_fingerprint": None,
        "confirmation_rounds": 0,
        "confirmation_required": False,
        "review_rounds": 0,
        "rematch_count": 0,
        "seat_count": 0,
        "would_block_count": 0,
        "blocked_count": 0,
        "override_count": 0,
        "last_transition": None,
        "last_failure_codes": [],
        "active_overrides": [],
        "seen_idempotency_keys": [],
        "updated_at": utc_now(),
    }


def load_workflow(session_dir: Path, default_mode: str = "enforce") -> dict[str, Any]:
    path = workflow_path(session_dir)
    if not path.exists():
        return default_state(load_session(session_dir), mode=default_mode)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_state(load_session(session_dir), mode=default_mode)
        base = default_state(load_session(session_dir), mode=str(data.get("mode") or default_mode))
        base.update(data)
        # Preserve existing mode from artefact (V3.3 shadow sessions stay shadow unless env)
        if data.get("mode") in ("shadow", "enforce"):
            base["mode"] = data["mode"]
        return base
    except json.JSONDecodeError:
        return default_state(load_session(session_dir), mode=default_mode)


def save_workflow(session_dir: Path, state: dict[str, Any]) -> None:
    state = dict(state)
    state["workflow_version"] = WORKFLOW_VERSION
    state["updated_at"] = utc_now()
    keys = state.get("seen_idempotency_keys") or []
    if isinstance(keys, list) and len(keys) > 128:
        state["seen_idempotency_keys"] = keys[-128:]
    path = workflow_path(session_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_workflow_event(session_dir: Path, event: dict[str, Any]) -> None:
    path = workflow_events_path(session_dir)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def already_seen(state: dict[str, Any], idempotency_key: str | None) -> bool:
    if not idempotency_key:
        return False
    return idempotency_key in (state.get("seen_idempotency_keys") or [])


def mark_seen(state: dict[str, Any], idempotency_key: str | None) -> None:
    if not idempotency_key:
        return
    keys = list(state.get("seen_idempotency_keys") or [])
    if idempotency_key not in keys:
        keys.append(idempotency_key)
    state["seen_idempotency_keys"] = keys


class WorkflowLock:
    """Best-effort exclusive lock for workflow.json updates (fcntl)."""

    def __init__(self, session_dir: Path):
        self.path = lock_path(session_dir)
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, *args):
        try:
            import fcntl
            if self._fh:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        if self._fh:
            self._fh.close()
