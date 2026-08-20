#!/usr/bin/env python3
"""Record a Yonko V3.4 workflow transition (shadow or enforce).

Usage:
  transition.py --session DIR --transition NAME [--data JSON] [--idempotency-key KEY]

Shadow: records would_block; exits 0; advances state to match underlying script success.
Enforce: confirmed guard violations exit non-zero and do not advance to success states
         for blocked transitions. Reporting failures unrelated to guards remain fail-open.

Protocol governs process. Evidence governs decisions.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import config as cfg  # noqa: E402
import guards  # noqa: E402
import state as st  # noqa: E402

# Transitions that must not advance destination state when blocked in enforce mode
BLOCK_NO_ADVANCE = frozenset({
    "finalize",
    "seat_reviewers",
    "pin_packet",
    "apply_or_revise",
    "human_approve_artifact",
    "rematch",
    "publish_evidence",
})


def record_transition(
    session_dir: Path,
    transition: str,
    data: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    data = data or {}
    session_dir = Path(session_dir)
    if not (session_dir / "session.json").exists():
        return {"ok": False, "skipped": True, "reason": "no_session"}

    with st.WorkflowLock(session_dir):
        return _record_locked(session_dir, transition, data, idempotency_key)


def _record_locked(
    session_dir: Path,
    transition: str,
    data: dict,
    idempotency_key: str | None,
) -> dict:
    existing = st.workflow_path(session_dir)
    prior = None
    if existing.exists():
        try:
            prior = json.loads(existing.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prior = None
    mode = cfg.resolve_mode(prior if isinstance(prior, dict) else None)
    wf = st.load_workflow(session_dir, default_mode=mode)
    wf["mode"] = mode
    from_state = wf.get("current_state") or "INIT"

    if not idempotency_key:
        idempotency_key = _default_idem_key(session_dir, transition, data, wf)

    if st.already_seen(wf, idempotency_key):
        return {
            "ok": True,
            "duplicate": True,
            "transition": transition,
            "idempotency_key": idempotency_key,
            "current_state": wf.get("current_state"),
            "would_block": False,
            "blocked": False,
            "failure_codes": [],
            "mode": mode,
            "exit_code": 0,
        }

    # Auto-invalidate when fingerprints diverge under a pin (mechanical)
    if transition == "collect_evidence" and wf.get("packet_hash"):
        if guards.stale_packet(session_dir, wf) or _fingerprints_diverged(session_dir, wf):
            _apply_invalidate(wf)
            inv_key = f"auto_invalidate:{st.evidence_fingerprint(session_dir)}:{st.docket_fingerprint(session_dir)}"
            if not st.already_seen(wf, inv_key):
                st.mark_seen(wf, inv_key)
                st.append_workflow_event(session_dir, _event(
                    session_dir, wf, "invalidate_packet", from_state, True, False, False, [],
                    inv_key, {"auto": True}, mode,
                ))
                from_state = wf.get("current_state") or from_state

    allowed, codes = guards.evaluate(session_dir, transition, wf, data)
    would_block = (not allowed) and mode == "shadow"
    blocked = (not allowed) and mode == "enforce"

    # Blocked attempts must not consume the success idempotency key (retry after fix).
    if blocked and idempotency_key:
        idempotency_key = f"{idempotency_key}:blocked:{','.join(codes)}"
        if st.already_seen(wf, idempotency_key):
            return {
                "ok": False,
                "duplicate": True,
                "transition": transition,
                "idempotency_key": idempotency_key,
                "current_state": wf.get("current_state"),
                "would_block": False,
                "blocked": True,
                "failure_codes": codes,
                "mode": mode,
                "exit_code": 2,
                "remediation": _remediation(codes),
            }

    session = st.load_session(session_dir)

    # Side effects only when allowed, or when shadow (observe-as-you-go)
    apply_side_effects = allowed or mode == "shadow"
    if apply_side_effects and not blocked:
        if transition == "pin_packet":
            wf["packet_hash"] = session.get("packet_hash")
            wf["packet_stale"] = False
            wf["docket_fingerprint"] = st.docket_fingerprint(session_dir)
            wf["evidence_fingerprint"] = st.evidence_fingerprint(session_dir)
            wf["linked_plan_fingerprint"] = st.linked_plan_fingerprint(session_dir)
        if transition == "collect_evidence":
            if not wf.get("packet_hash"):
                wf["evidence_fingerprint"] = st.evidence_fingerprint(session_dir)
        if transition == "invalidate_packet":
            _apply_invalidate(wf)
        if transition == "seat_reviewers":
            count = int(data.get("seat_count") or data.get("count") or 0)
            if count:
                wf["seat_count"] = max(int(wf.get("seat_count") or 0), count)
            if session.get("review_type") == "plan" and data.get("confirmation_round") is True:
                wf["confirmation_rounds"] = int(wf.get("confirmation_rounds") or 0) + 1
                wf["review_rounds"] = int(wf.get("review_rounds") or 0) + 1
        if transition == "apply_or_revise" and session.get("review_type") == "plan":
            accepted_material = bool(data.get("accepted_medium_or_higher"))
            material_leaf_revision = bool(data.get("material_leaf_revision"))
            explicitly_non_material = (
                data.get("accepted_medium_or_higher") is False
                and data.get("material_leaf_revision") is False
            )
            if accepted_material or material_leaf_revision or not explicitly_non_material:
                wf["confirmation_required"] = True
        if transition == "rematch" and session.get("review_type") in ("plan", "document"):
            if session.get("review_type") == "document":
                wf["confirmation_rounds"] = int(wf.get("confirmation_rounds") or 0) + 1
            wf["rematch_count"] = int(wf.get("rematch_count") or 0) + 1
        if transition == "human_override_legality":
            entry = {
                "codes": list(data.get("codes") or []),
                "reason": data.get("reason"),
                "approved_by": data.get("approved_by") or data.get("overridden_by"),
                "at": st.utc_now(),
            }
            ov = list(wf.get("active_overrides") or [])
            ov.append(entry)
            wf["active_overrides"] = ov
            wf["override_count"] = int(wf.get("override_count") or 0) + 1
            # Persist override artefact for audit
            st.override_path(session_dir).write_text(
                json.dumps({
                    "codes": entry["codes"],
                    "reason": entry["reason"],
                    "approved_by": entry["approved_by"],
                    "at": entry["at"],
                    "would_block_evidence": data.get("would_block_evidence") or codes,
                }, indent=2) + "\n",
                encoding="utf-8",
            )

    if would_block:
        wf["would_block_count"] = int(wf.get("would_block_count") or 0) + 1
    if blocked:
        wf["blocked_count"] = int(wf.get("blocked_count") or 0) + 1

    # State advance
    target = st.TRANSITIONS.get(transition, {}).get("to")
    if transition == "human_override_legality":
        pass  # no state change
    elif blocked and transition in BLOCK_NO_ADVANCE:
        pass  # stay in from_state
    elif target:
        wf["current_state"] = target
    elif transition == "invalidate_packet":
        wf["current_state"] = "RISK_SET"

    wf["review_type"] = session.get("review_type") or wf.get("review_type")
    wf["artifact_type"] = session.get("artifact_type")
    wf["last_transition"] = transition
    wf["last_failure_codes"] = codes
    st.mark_seen(wf, idempotency_key)
    st.save_workflow(session_dir, wf)

    event = _event(
        session_dir, wf, transition, from_state, allowed, would_block, blocked, codes,
        idempotency_key, data, mode,
    )
    st.append_workflow_event(session_dir, event)

    exit_code = 2 if blocked else 0
    return {
        "ok": not blocked,
        "duplicate": False,
        "transition": transition,
        "would_block": would_block,
        "blocked": blocked,
        "allowed": allowed,
        "failure_codes": codes,
        "current_state": wf.get("current_state"),
        "from_state": from_state,
        "would_block_count": wf.get("would_block_count"),
        "blocked_count": wf.get("blocked_count"),
        "idempotency_key": idempotency_key,
        "mode": mode,
        "exit_code": exit_code,
        "remediation": _remediation(codes) if blocked else None,
    }


def _fingerprints_diverged(session_dir: Path, wf: dict) -> bool:
    pe = wf.get("evidence_fingerprint")
    pd = wf.get("docket_fingerprint")
    pp = wf.get("linked_plan_fingerprint")
    if pe and st.evidence_fingerprint(session_dir) and pe != st.evidence_fingerprint(session_dir):
        return True
    if pd and st.docket_fingerprint(session_dir) and pd != st.docket_fingerprint(session_dir):
        return True
    if pp and st.linked_plan_fingerprint(session_dir) and pp != st.linked_plan_fingerprint(session_dir):
        return True
    return False


def _apply_invalidate(wf: dict) -> None:
    wf["packet_hash"] = None
    wf["packet_stale"] = True
    wf["docket_fingerprint"] = None
    wf["linked_plan_fingerprint"] = None
    # Keep evidence_fingerprint as last-known for audit; clear pin association
    wf["current_state"] = "RISK_SET"


def _event(
    session_dir: Path,
    wf: dict,
    transition: str,
    from_state: str,
    allowed: bool,
    would_block: bool,
    blocked: bool,
    codes: list,
    idempotency_key: str | None,
    data: dict,
    mode: str,
) -> dict:
    session = st.load_session(session_dir)
    clean = {k: v for k, v in data.items() if not str(k).startswith("_")}
    # Strip bulky / sensitive payloads
    for k in list(clean.keys()):
        if k in ("diff", "patch", "packet", "content", "body"):
            clean.pop(k, None)
    return {
        "event_schema_version": st.EVENT_SCHEMA_VERSION,
        "workflow_version": st.WORKFLOW_VERSION,
        "ts": st.utc_now(),
        "session_id": session.get("session_id"),
        "review_type": session.get("review_type") or wf.get("review_type"),
        "transition": transition,
        "from_state": from_state,
        "to_state": wf.get("current_state"),
        "mode": mode,
        "allowed": allowed,
        "would_block": would_block,
        "blocked": blocked,
        "failure_codes": codes,
        "idempotency_key": idempotency_key,
        "packet_hash": session.get("packet_hash") or wf.get("packet_hash"),
        "data": clean,
    }


def _remediation(codes: list[str]) -> dict[str, str]:
    tips = {
        "OPEN_MATERIAL_FINDINGS": "Resolve or drop medium/high findings per disposition contract, then retry finalize.",
        "VERIFICATION_REQUIRED": "Record successful verification_completed or scoped_verify (green/confirmed).",
        "REVIEWER_INCOMPLETE": "Seat the configured minimum reviewers for this risk band (and cover routing.json seats when present).",
        "OPENCODE_EXECUTE_MISSING": "OpenCode seats are still awaiting_chair_dispatch - spawn Cursor Tasks that Shell-run dispatch.execute_command (invoke-seat --execute), then retry.",
        "ORG_SHIP_GATE_REQUIRED": "Before finalize --verdict pass, run scripts/run-org-ship-gate.sh (OpenCode Go GPT Luna hostile org ship gate) when the adapter enables it. Council Content is not enough.",
        "ORG_SHIP_GATE_FAILED": "Org ship gate Remanded or Attack card incomplete. Fix findings (or re-run the gate after fixes) - do not finalize Pass.",
        "HUMAN_APPROVAL_REQUIRED": "Run workflow/approve.py with --approved-by <human> after creating the approved/final artefact.",
        "PACKET_STALE": "Regenerate packet (sanitise-and-hash-packet) to repin after Docket/evidence changes.",
        "PACKET_HASH_MISMATCH": "Regenerate and pin packet; ensure session.packet_hash matches packet.md.",
        "BUDGET_EXCEEDED": "Confirmation/rematch budget exhausted; do not increment further without policy change.",
        "PLAN_CONFIRMATION_REQUIRED": "Re-hash PLAN.revised.md and run one full confirmation round before finalizing Pass.",
        "WRITE_POLICY_VIOLATION": "Plan/document must not production-apply; implementation must not misuse human_approve.",
        "ILLEGAL_TRANSITION": "Complete prior mechanical steps (evidence -> risk -> pin -> seat) before this transition.",
        "PRECONDITION_FAILED": "Required session artefact for this transition is missing.",
    }
    return {c: tips.get(c, "See workflow README.") for c in codes}


def _default_idem_key(
    session_dir: Path, transition: str, data: dict, wf: dict
) -> str | None:
    session = st.load_session(session_dir)
    if transition == "initialise":
        return f"initialise:{session.get('session_id')}"
    if transition == "pin_packet":
        h = session.get("packet_hash") or ""
        fp = f"{st.docket_fingerprint(session_dir)}:{st.evidence_fingerprint(session_dir)}"
        return f"pin_packet:{h}:{st.sha256_text(fp)}" if h else None
    if transition == "classify_risk":
        risk = session_dir / "evidence" / "risk.json"
        scope = session_dir / "evidence" / "scope-risk.json"
        p = risk if risk.exists() else scope
        if p.exists():
            return f"classify_risk:{st.sha256_bytes(p.read_bytes())}"
        return "classify_risk:missing"
    if transition == "collect_evidence":
        fp = st.evidence_fingerprint(session_dir) or "none"
        return f"collect_evidence:{fp}"
    if transition == "seat_reviewers":
        c = data.get("seat_count") or data.get("count") or wf.get("seat_count") or 0
        h = session.get("packet_hash") or ""
        return f"seat_reviewers:{h}:{c}"
    if transition == "validate_findings":
        return f"validate_findings:{session.get('packet_hash')}:{data.get('kind')}"
    if transition == "verify":
        return f"verify:{session.get('packet_hash')}:{data.get('verdict') or 'x'}"
    if transition == "scoped_verify":
        return f"scoped_verify:{data.get('result')}:{session.get('packet_hash')}"
    if transition == "apply_or_revise":
        n = int(wf.get("confirmation_rounds") or 0)
        return f"apply_or_revise:{n}:{data.get('artifact') or data.get('accepted') or 'x'}"
    if transition == "finalize":
        # Include failure fingerprint so a blocked attempt then a successful retry are distinct
        return f"finalize:{data.get('verdict')}:{session.get('packet_hash')}"
    if transition == "publish_evidence":
        return f"publish_evidence:{data.get('candidate_hash') or data.get('phase') or 'x'}"
    if transition == "invalidate_packet":
        return f"invalidate:{st.docket_fingerprint(session_dir)}:{st.evidence_fingerprint(session_dir)}"
    if transition == "human_approve_artifact":
        return f"human_approve:{data.get('artifact')}:{data.get('artifact_hash') or data.get('approved_by')}"
    if transition == "human_override_legality":
        codes = ",".join(sorted(str(c) for c in (data.get("codes") or [])))
        return f"override:{codes}:{st.sha256_text(str(data.get('reason') or ''))[:16]}"
    return f"{transition}:{st.utc_now()}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Yonko V3.4 workflow transition")
    parser.add_argument("--session", required=True)
    parser.add_argument("--transition", required=True)
    parser.add_argument("--data", default="{}")
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args()
    try:
        data = json.loads(args.data) if args.data else {}
        if not isinstance(data, dict):
            data = {}
        result = record_transition(
            Path(args.session), args.transition, data, args.idempotency_key
        )
        print(json.dumps(result, separators=(",", ":")))
        return int(result.get("exit_code") or 0)
    except Exception as e:
        # Fail-open for implementation/reporting failures (not confirmed guard violations)
        try:
            err = Path(args.session) / "workflow.error.txt"
            err.write_text(f"{e}\n{traceback.format_exc()}\n", encoding="utf-8")
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": str(e), "fail_open": True, "exit_code": 0}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
