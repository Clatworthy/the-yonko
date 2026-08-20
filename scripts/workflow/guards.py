"""Yonko V3.4 workflow guards - evaluate legality from session artefacts only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config as cfg
from state import (
    FAILURE_CODES,
    TRANSITIONS,
    approval_path,
    docket_fingerprint,
    evidence_fingerprint,
    linked_plan_fingerprint,
    load_session,
    override_path,
    sha256_bytes,
)

CLOSED_DISPOSITIONS = frozenset({
    "drop", "dropped", "note", "notes", "resolved", "fixed", "applied", "discard",
})
OPEN_BUCKETS = ("accepted", "held", "findings", "plan_findings", "document_findings")
CLOSED_BUCKETS = ("dropped", "notes", "dropped_findings")
CHAIR_ALIASES = frozenset({
    "chair", "zoro", "yonko", "system", "auto", "bot", "agent", "parent",
})


def _risk(session_dir: Path) -> dict[str, Any]:
    for name in ("risk.json", "scope-risk.json"):
        p = session_dir / "evidence" / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
    return {}


def _band(session_dir: Path, session: dict[str, Any]) -> str:
    r = _risk(session_dir)
    return str(r.get("risk") or session.get("risk") or "medium").lower()


def _opencode_execute_missing(session_dir: Path) -> bool:
    """True when a routed OpenCode seat never ran --execute (stuck on dispatch)."""
    routing_path = session_dir / "evidence" / "routing.json"
    if not routing_path.is_file():
        return False
    try:
        seats = json.loads(routing_path.read_text(encoding="utf-8")).get("seats") or []
    except json.JSONDecodeError:
        return False
    for seat in seats:
        runtime_dir = session_dir / "runtime" / str(seat)
        findings = runtime_dir / "findings.json"
        if findings.is_file():
            continue
        result_path = runtime_dir / "result.json"
        dispatch_path = runtime_dir / "dispatch.json"
        awaiting = False
        is_opencode = False
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("skipped_by_routing"):
                    continue
                awaiting = bool(result.get("awaiting_chair_dispatch"))
                is_opencode = result.get("runtime") == "opencode"
            except json.JSONDecodeError:
                pass
        if dispatch_path.is_file():
            try:
                dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
                if dispatch.get("runtime") == "opencode" or (
                    " --execute" in str(dispatch.get("execute_command") or "")
                ):
                    is_opencode = True
            except json.JSONDecodeError:
                pass
        if is_opencode and (awaiting or not result_path.is_file()):
            return True
    return False


def _findings_open_material(session_dir: Path) -> bool:
    """True if unresolved medium/high/critical findings remain (disposition contract)."""
    for name in ("findings.json", "plan_findings.json", "document_findings.json", "findings.raw.json"):
        p = session_dir / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [x for x in data if isinstance(x, dict)]
        elif isinstance(data, dict):
            # Bucket form: accepted/held are open; dropped/notes are closed
            if any(k in data for k in ("accepted", "held", "dropped", "notes")):
                for key in ("accepted", "held"):
                    arr = data.get(key) or []
                    if isinstance(arr, list):
                        items.extend(x for x in arr if isinstance(x, dict))
            else:
                for key in OPEN_BUCKETS:
                    arr = data.get(key)
                    if isinstance(arr, list):
                        items.extend(x for x in arr if isinstance(x, dict))
                        break
        for f in items:
            if not _is_open_finding(f):
                continue
            sev = str(f.get("severity") or "").lower()
            if sev in ("medium", "high", "critical"):
                return True
    return False


def _is_open_finding(f: dict[str, Any]) -> bool:
    disp = str(
        f.get("disposition") or f.get("action") or f.get("status") or ""
    ).lower().strip()
    if disp in CLOSED_DISPOSITIONS:
        return False
    return True


def _events(session_dir: Path) -> list[dict[str, Any]]:
    p = session_dir / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _has_event(session_dir: Path, etype: str) -> bool:
    return any(e.get("type") == etype for e in _events(session_dir))


def _verification_ok(session_dir: Path) -> tuple[bool, bool]:
    """Return (present, successful). Present means a verify signal exists."""
    present = False
    success = False
    failed = False
    for e in _events(session_dir):
        if e.get("type") == "verification_completed":
            present = True
            d = e.get("data") or {}
            v = str(d.get("verdict") or "").lower()
            if v in ("confirmed", "pass", "ok", "success"):
                success = True
            elif v in ("rejected", "fail", "failed"):
                failed = True
            if int(d.get("confirmed") or 0) > 0:
                success = True
            if int(d.get("rejected") or 0) > 0 and not success:
                failed = True
        if e.get("type") == "scoped_verify":
            present = True
            d = e.get("data") or {}
            r = str(d.get("result") or d.get("verdict") or "").lower()
            if r in ("green", "pass", "ok", "success", "confirmed"):
                success = True
            elif r in ("red", "fail", "failed", "rejected"):
                failed = True
    for name in ("verification.json", "verifications.json"):
        p = session_dir / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        present = True
        items = data if isinstance(data, list) else data.get("verifications") or (
            [data] if isinstance(data, dict) and "verdict" in data else []
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            v = str(item.get("verdict") or "").lower()
            if v == "confirmed":
                success = True
            elif v == "rejected":
                failed = True
    if success:
        return True, True
    if present and failed:
        return True, False
    if present:
        return True, False  # present but not clearly successful
    return False, False


def _min_seats(session_dir: Path, session: dict[str, Any]) -> int:
    routing_path = session_dir / "evidence" / "routing.json"
    if routing_path.exists():
        try:
            routing = json.loads(routing_path.read_text(encoding="utf-8"))
            if isinstance(routing.get("effective_floor"), int):
                return max(1, int(routing["effective_floor"]))
            seats = routing.get("seats")
            if isinstance(seats, list) and seats:
                return max(1, len(seats))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    risk = _risk(session_dir)
    if "reviewers" in risk and isinstance(risk["reviewers"], int):
        return max(1, int(risk["reviewers"]))
    review_type = session.get("review_type") or "implementation"
    return cfg.min_seats(str(review_type), _band(session_dir, session))


def _required_routing_seats(session_dir: Path) -> set[str]:
    """V3.5: required council seat ids from routing.json (empty if absent)."""
    routing_path = session_dir / "evidence" / "routing.json"
    if not routing_path.exists():
        return set()
    try:
        routing = json.loads(routing_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    seats = routing.get("seats") or []
    if not isinstance(seats, list):
        return set()
    return {str(s).lower() for s in seats if s}


def _seated_seat_ids(session_dir: Path, data: dict[str, Any]) -> set[str]:
    raw = data.get("seats")
    if isinstance(raw, list) and raw:
        return {str(s).lower() for s in raw if s}
    for e in reversed(_events(session_dir)):
        if e.get("type") == "reviewers_seated":
            seats = (e.get("data") or {}).get("seats")
            if isinstance(seats, list) and seats:
                return {str(s).lower() for s in seats if s}
            break
    return set()


def _routing_seats_covered(session_dir: Path, data: dict[str, Any]) -> bool:
    required = _required_routing_seats(session_dir)
    if not required:
        return True
    seated = _seated_seat_ids(session_dir, data)
    return required.issubset(seated)


def _max_confirmation_rounds(session_dir: Path, session: dict[str, Any]) -> int:
    rt = session.get("review_type") or "implementation"
    risk = _risk(session_dir)
    if "max_confirmation_rounds" in risk and isinstance(risk["max_confirmation_rounds"], int):
        return int(risk["max_confirmation_rounds"])
    return cfg.max_confirmation_rounds(str(rt))


def _confirmation_rounds(session_dir: Path, wf: dict[str, Any]) -> int:
    n = int(wf.get("confirmation_rounds") or 0)
    n_ev = sum(1 for e in _events(session_dir) if e.get("type") == "artifact_revised")
    return max(n, n_ev)


def _packet_hash_ok(session_dir: Path, session: dict[str, Any]) -> bool:
    packet = session_dir / "packet.md"
    expected = session.get("packet_hash")
    if not packet.is_file() or not expected:
        return False
    return sha256_bytes(packet.read_bytes()) == expected


def stale_packet(session_dir: Path, wf: dict[str, Any]) -> bool:
    if wf.get("packet_stale"):
        return True
    if not wf.get("packet_hash"):
        return False
    pinned_d = wf.get("docket_fingerprint")
    pinned_e = wf.get("evidence_fingerprint")
    pinned_p = wf.get("linked_plan_fingerprint")
    if pinned_d is None and pinned_e is None and pinned_p is None:
        return False
    cur_d = docket_fingerprint(session_dir)
    cur_e = evidence_fingerprint(session_dir)
    cur_p = linked_plan_fingerprint(session_dir)
    if pinned_d and cur_d and pinned_d != cur_d:
        return True
    if pinned_e and cur_e and pinned_e != cur_e:
        return True
    if pinned_p and cur_p and pinned_p != cur_p:
        return True
    return False


def _load_approval(session_dir: Path) -> dict[str, Any] | None:
    p = approval_path(session_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _approval_valid(session_dir: Path, session: dict[str, Any], review_type: str) -> bool:
    appr = _load_approval(session_dir)
    if not appr:
        return False
    by = str(appr.get("approved_by") or "").strip()
    if not by or by.lower() in CHAIR_ALIASES:
        return False
    artifact = str(appr.get("artifact") or "")
    if review_type == "plan":
        if artifact and artifact != "PLAN.approved.md":
            return False
        if not (session_dir / "PLAN.approved.md").exists():
            return False
        expected_hash = appr.get("artifact_hash")
        if expected_hash:
            actual = sha256_bytes((session_dir / "PLAN.approved.md").read_bytes())
            if actual != expected_hash:
                return False
        return True
    if review_type == "document":
        at = session.get("artifact_type") or ""
        finals = {
            "pap": "PAP.final.md",
            "prd": "PRD.final.md",
            "adr": "ADR.final.md",
            "design": "DESIGN.final.md",
        }
        fname = finals.get(str(at))
        if not fname or not (session_dir / fname).exists():
            return False
        if artifact and artifact != fname:
            return False
        expected_hash = appr.get("artifact_hash")
        if expected_hash:
            actual = sha256_bytes((session_dir / fname).read_bytes())
            if actual != expected_hash:
                return False
        return True
    return False


def _active_override_codes(session_dir: Path, wf: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for item in wf.get("active_overrides") or []:
        if isinstance(item, dict):
            for c in item.get("codes") or []:
                codes.add(str(c))
    p = override_path(session_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for c in data.get("codes") or []:
                    codes.add(str(c))
        except json.JSONDecodeError:
            pass
    return codes


def _allowed_from(transition: str, current: str) -> bool:
    meta = TRANSITIONS.get(transition) or {}
    allowed = meta.get("from")
    if allowed is None:
        return True
    if current in allowed:
        return True
    # None in set means INIT-only / fresh
    if None in allowed and current in (None, "INIT"):
        return True
    return False


def _dedupe(codes: list[str]) -> list[str]:
    out: list[str] = []
    for c in codes:
        if c in FAILURE_CODES and c not in out:
            out.append(c)
    return out


def evaluate(
    session_dir: Path,
    transition: str,
    wf: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Return (allowed, failure_codes). Does not mutate state."""
    data = data or {}
    codes: list[str] = []
    session = load_session(session_dir)
    review_type = session.get("review_type") or wf.get("review_type") or "implementation"
    current = wf.get("current_state") or "INIT"

    if transition not in TRANSITIONS:
        return False, ["ILLEGAL_TRANSITION"]

    if transition == "human_override_legality":
        reason = str(data.get("reason") or "").strip()
        by = str(data.get("approved_by") or data.get("overridden_by") or "").strip()
        ocodes = data.get("codes") or []
        if not reason or not by or by.lower() in CHAIR_ALIASES or not ocodes:
            return False, ["PRECONDITION_FAILED"]
        # Never allow override to bypass Evidence Index publication gate
        forbidden = {"EVIDENCE_INDEX_PUBLISH"}  # reserved; not a workflow failure code
        if any(str(c) in forbidden for c in ocodes):
            return False, ["WRITE_POLICY_VIOLATION"]
        return True, []

    if transition == "initialise":
        return True, []

    if not _allowed_from(transition, current):
        # Soft exceptions: re-entrant same-state transitions already listed in from-sets
        codes.append("ILLEGAL_TRANSITION")

    if transition == "finalize":
        if not _packet_hash_ok(session_dir, session):
            if session.get("packet_hash"):
                codes.append("PACKET_HASH_MISMATCH")
            else:
                codes.append("PRECONDITION_FAILED")
        if stale_packet(session_dir, wf):
            codes.append("PACKET_STALE")
        band = _band(session_dir, session)
        seat_count = int(data.get("seat_count") or wf.get("seat_count") or 0)
        if not _has_event(session_dir, "reviewers_seated") and seat_count < 1:
            codes.append("REVIEWER_INCOMPLETE")
        elif seat_count and seat_count < _min_seats(session_dir, session):
            codes.append("REVIEWER_INCOMPLETE")
        elif _has_event(session_dir, "reviewers_seated") and seat_count < _min_seats(session_dir, session):
            # Prefer recorded seat_count; if zero, parse last event
            if seat_count < 1:
                for e in reversed(_events(session_dir)):
                    if e.get("type") == "reviewers_seated":
                        seat_count = int((e.get("data") or {}).get("count") or 0)
                        break
            if seat_count < _min_seats(session_dir, session):
                codes.append("REVIEWER_INCOMPLETE")
        if not _routing_seats_covered(session_dir, data):
            codes.append("REVIEWER_INCOMPLETE")
        if _opencode_execute_missing(session_dir):
            codes.append("OPENCODE_EXECUTE_MISSING")
        # Post-council org ship gate: council Content is not enough when enabled.
        _verdict_for_gate = str(
            data.get("verdict") or session.get("verdict") or ""
        ).lower()
        if _verdict_for_gate == "pass":
            try:
                import sys as _sys

                _lib = Path(__file__).resolve().parents[1] / "lib"
                if str(_lib) not in _sys.path:
                    _sys.path.insert(0, str(_lib))
                import org_ship_gate as _sbg  # noqa: E402

                _gate = _sbg.validate_session_gate(session_dir)
                if _gate.get("required") and not _gate.get("ok"):
                    code = _gate.get("code") or "ORG_SHIP_GATE_REQUIRED"
                    if code in FAILURE_CODES and code not in codes:
                        codes.append(code)
            except Exception:
                pass
        present, ok = _verification_ok(session_dir)
        if cfg.verify_required(band):
            if not present or not ok:
                codes.append("VERIFICATION_REQUIRED")
        # V3.5: routing may require verifier even when band would not
        routing_path = session_dir / "evidence" / "routing.json"
        if routing_path.exists():
            try:
                routing = json.loads(routing_path.read_text(encoding="utf-8"))
                if routing.get("require_verifier"):
                    present2, ok2 = _verification_ok(session_dir)
                    if not present2 or not ok2:
                        if "VERIFICATION_REQUIRED" not in codes:
                            codes.append("VERIFICATION_REQUIRED")
            except json.JSONDecodeError:
                pass
        verdict = str(data.get("verdict") or session.get("verdict") or "").lower()
        if verdict == "pass" and _findings_open_material(session_dir):
            codes.append("OPEN_MATERIAL_FINDINGS")
        if review_type in ("plan", "document"):
            conf = _confirmation_rounds(session_dir, wf)
            if conf > _max_confirmation_rounds(session_dir, session):
                codes.append("BUDGET_EXCEEDED")
            if (
                review_type == "plan"
                and verdict == "pass"
                and wf.get("confirmation_required")
                and conf < 1
            ):
                codes.append("PLAN_CONFIRMATION_REQUIRED")
            if verdict == "pass" and not _approval_valid(session_dir, session, review_type):
                codes.append("HUMAN_APPROVAL_REQUIRED")

    elif transition == "seat_reviewers":
        if not _packet_hash_ok(session_dir, session):
            codes.append("PACKET_HASH_MISMATCH")
        if stale_packet(session_dir, wf):
            codes.append("PACKET_STALE")
        seat_count = int(data.get("seat_count") or data.get("count") or wf.get("seat_count") or 0)
        if seat_count < _min_seats(session_dir, session):
            codes.append("REVIEWER_INCOMPLETE")
        if not _routing_seats_covered(session_dir, data):
            codes.append("REVIEWER_INCOMPLETE")
        cited = data.get("packet_hash")
        if cited and session.get("packet_hash") and cited != session.get("packet_hash"):
            codes.append("PACKET_HASH_MISMATCH")
        if review_type == "plan" and data.get("confirmation_round") is True:
            conf = _confirmation_rounds(session_dir, wf) + 1
            if conf > _max_confirmation_rounds(session_dir, session):
                codes.append("BUDGET_EXCEEDED")

    elif transition == "pin_packet":
        if current == "INIT":
            codes.append("ILLEGAL_TRANSITION")
        if not (session_dir / "packet.md").exists():
            codes.append("PRECONDITION_FAILED")
        if not _packet_hash_ok(session_dir, session):
            codes.append("PACKET_HASH_MISMATCH")

    elif transition == "collect_evidence":
        if wf.get("packet_hash") and (
            stale_packet(session_dir, wf)
            or (
                wf.get("evidence_fingerprint")
                and evidence_fingerprint(session_dir)
                and evidence_fingerprint(session_dir) != wf.get("evidence_fingerprint")
            )
        ):
            codes.append("PACKET_STALE")

    elif transition == "verify":
        if not _allowed_from(transition, current) and "ILLEGAL_TRANSITION" not in codes:
            pass

    elif transition == "apply_or_revise":
        if review_type in ("plan", "document"):
            if data.get("writes_production_code") is True or data.get("production_apply") is True:
                codes.append("WRITE_POLICY_VIOLATION")
        if review_type == "implementation" and data.get("human_approve_as_verify"):
            codes.append("WRITE_POLICY_VIOLATION")

    elif transition == "human_approve_artifact":
        if review_type not in ("plan", "document"):
            codes.append("WRITE_POLICY_VIOLATION")
        by = str(data.get("approved_by") or "").strip()
        if not by or by.lower() in CHAIR_ALIASES:
            codes.append("HUMAN_APPROVAL_REQUIRED")
        if not data.get("artifact"):
            codes.append("HUMAN_APPROVAL_REQUIRED")

    elif transition == "publish_evidence":
        if current != "FINALIZED" and not (session_dir / "SUMMARY.md").exists():
            codes.append("PRECONDITION_FAILED")

    elif transition == "rematch":
        if review_type in ("plan", "document"):
            if _confirmation_rounds(session_dir, wf) >= _max_confirmation_rounds(session_dir, session):
                codes.append("BUDGET_EXCEEDED")

    out = _dedupe(codes)
    # Apply human overrides (narrow): remove matching codes only
    overrides = _active_override_codes(session_dir, wf)
    if overrides:
        out = [c for c in out if c not in overrides]
    return len(out) == 0, out
