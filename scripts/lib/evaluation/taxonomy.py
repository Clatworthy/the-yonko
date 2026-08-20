"""Finding disposition taxonomy - honest unknown, never invent rejected_*."""

from __future__ import annotations

import re
from typing import Any

DISPOSITIONS = (
    "accepted",
    "accepted_as_sibling",
    "merged",
    "duplicate",
    "downgraded",
    "rejected_false",
    "rejected_unsupported",
    "rejected_out_of_scope",
    "rejected_unreachable",
    "rejected_pre_existing_not_worsened",
    "verifier_inconclusive",
    "chair_inconclusive",
    "unknown_not_adjudicated",
)

ADJUDICATION_STATES = (
    "complete",
    "partial",
    "pre_adjudication",
    "plan_array_form",
    "document_array_form",
    "empty_findings",
)

# Documented substring heuristics (casefold). Empty reason → unknown_not_adjudicated.
UNSUPPORTED_HINTS = (
    "unsupported",
    "no evidence",
    "ungrounded",
    "not grounded",
    "lacks evidence",
    "without evidence",
)
OUT_OF_SCOPE_HINTS = (
    "out of scope",
    "out-of-scope",
    "oos",
    "not in scope",
    "beyond scope",
)
PREEXISTING_HINTS = (
    "pre-existing",
    "preexisting",
    "pre existing",
    "not worsened",
    "already present",
)


def _reason_text(f: dict[str, Any]) -> str:
    return str(f.get("reason") or f.get("drop_reason") or f.get("chair_reason") or "").strip()


def map_dropped_disposition(f: dict[str, Any]) -> str:
    """Map dropped finding. Never invent rejected_* from empty reasons."""
    action = str(f.get("action") or "").strip().lower()
    reason = _reason_text(f)
    before = f.get("original_severity") or f.get("severity_before") or f.get("seat_severity")
    after = f.get("severity")
    if before and after and str(before).lower() != str(after).lower():
        # Severity change on a dropped item is unusual; still record downgraded if present.
        pass

    if action in ("drop", "dropped", "reject", "rejected", ""):
        if not reason:
            return "unknown_not_adjudicated"
        low = reason.casefold()
        if any(h in low for h in UNSUPPORTED_HINTS):
            return "rejected_unsupported"
        if any(h in low for h in OUT_OF_SCOPE_HINTS):
            return "rejected_out_of_scope"
        if any(h in low for h in PREEXISTING_HINTS):
            return "rejected_pre_existing_not_worsened"
        return "unknown_not_adjudicated"

    # applied / resolved / note-like actions are not rejected_* invents
    if action in ("applied", "resolved", "fixed", "note"):
        return "unknown_not_adjudicated"
    return "unknown_not_adjudicated"


def map_held_disposition(f: dict[str, Any]) -> str:
    reason = _reason_text(f)
    if reason and "inconclusive" in reason.casefold():
        return "chair_inconclusive"
    return "chair_inconclusive" if reason else "unknown_not_adjudicated"


def map_accepted_disposition(f: dict[str, Any], *, duplicate: bool = False) -> str:
    if duplicate:
        return "duplicate"
    before = f.get("original_severity") or f.get("severity_before") or f.get("seat_severity")
    after = f.get("severity")
    if before and after and str(before).lower() != str(after).lower():
        return "downgraded"
    return "accepted"


def seat_from_finding(f: dict[str, Any]) -> str | None:
    seat = f.get("reviewer") or f.get("seat")
    if isinstance(seat, str) and seat.strip():
        return seat.strip().lower()
    sources = f.get("sources")
    if isinstance(sources, list) and sources:
        # Map Bb1 / Lf3 style prefixes when reviewer absent
        src = str(sources[0])
        prefix = re.match(r"^([A-Za-z]+)", src)
        if not prefix:
            return None
        p = prefix.group(1).lower()
        mapping = {
            "sh": "shanks",
            "bb": "blackbeard",
            "bu": "buggy",
            "lf": "luffy",
            "sk": "shanks",
        }
        for k, v in mapping.items():
            if p.startswith(k):
                return v
        if p in ("shanks", "blackbeard", "buggy", "luffy"):
            return p
    return None
