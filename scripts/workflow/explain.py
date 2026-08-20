#!/usr/bin/env python3
"""Deterministic workflow explain - read-only audit view. No AI. No mutation.

Usage:
  explain.py --session DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import state as st  # noqa: E402


def explain(session_dir: Path) -> str:
    session_dir = Path(session_dir)
    lines: list[str] = []
    session = st.load_session(session_dir)
    wf_path = st.workflow_path(session_dir)
    if not wf_path.exists():
        lines.append("workflow: absent (legacy session - unknown historical legality)")
        lines.append(f"session_id: {session.get('session_id')}")
        lines.append(f"review_type: {session.get('review_type')}")
        return "\n".join(lines) + "\n"

    wf = st.load_workflow(session_dir)
    lines.append("=== Yonko workflow explain (deterministic) ===")
    lines.append(f"session_id: {session.get('session_id')}")
    lines.append(f"review_type: {wf.get('review_type') or session.get('review_type')}")
    lines.append(f"mode: {wf.get('mode')}")
    lines.append(f"current_state: {wf.get('current_state')}")
    lines.append(f"packet_hash: {wf.get('packet_hash') or session.get('packet_hash')}")
    lines.append(f"packet_stale: {wf.get('packet_stale')}")
    lines.append(f"seat_count: {wf.get('seat_count')}")
    lines.append(f"confirmation_rounds: {wf.get('confirmation_rounds')}")
    lines.append(f"rematch_count: {wf.get('rematch_count')}")
    lines.append(f"would_block_count: {wf.get('would_block_count')}")
    lines.append(f"blocked_count: {wf.get('blocked_count')}")
    lines.append(f"override_count: {wf.get('override_count')}")

    # V3.5 reviewer routing explain (deterministic; no AI)
    routing_path = session_dir / "evidence" / "routing.json"
    if routing_path.exists():
        try:
            routing = json.loads(routing_path.read_text(encoding="utf-8"))
            lines.append("")
            lib = Path(__file__).resolve().parents[1] / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            import routing as route_mod  # noqa: E402

            lines.append(route_mod.explain_routing(routing).rstrip())
        except Exception as e:  # noqa: BLE001 - explain must not crash
            lines.append("")
            lines.append(f"=== Selected reviewers (V3.5 routing) ===")
            lines.append(f"(unreadable routing.json: {e})")
    elif (session_dir / "evidence" / "change-classes.json").exists():
        lines.append("")
        lines.append("=== Selected reviewers (V3.5 routing) ===")
        lines.append("(change-classes.json present; run route-reviewers.sh)")

    lines.append("")
    lines.append("--- transitions ---")
    ep = st.workflow_events_path(session_dir)
    if not ep.exists():
        lines.append("(no workflow-events.jsonl)")
    else:
        for line in ep.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                lines.append("! malformed event (ignored as proof)")
                continue
            flag = ""
            if ev.get("blocked"):
                flag = " BLOCKED"
            elif ev.get("would_block"):
                flag = " WOULD_BLOCK"
            codes = ",".join(ev.get("failure_codes") or []) or "-"
            lines.append(
                f"{ev.get('ts')}  {ev.get('transition')}: "
                f"{ev.get('from_state')} -> {ev.get('to_state')} "
                f"allowed={ev.get('allowed')}{flag} codes=[{codes}]"
            )
            d = ev.get("data") or {}
            if ev.get("transition") == "human_override_legality":
                lines.append(
                    f"    override by={d.get('approved_by')} reason={d.get('reason')!r} "
                    f"codes={d.get('codes')}"
                )
            if ev.get("transition") == "human_approve_artifact":
                h = (d.get("artifact_hash") or "")[:12]
                lines.append(
                    f"    approved_by={d.get('approved_by')} artifact={d.get('artifact')} hash={h}..."
                )
            if ev.get("transition") == "invalidate_packet":
                lines.append("    packet invalidated (repin required)")
            if ev.get("transition") == "pin_packet":
                ph = (ev.get("packet_hash") or "")[:16]
                lines.append(f"    pin packet_hash={ph}...")
    lines.append("")
    lines.append("--- final protocol status ---")
    if wf.get("current_state") == "FINALIZED":
        lines.append("FINALIZED")
    elif wf.get("last_failure_codes"):
        lines.append(f"last_failure_codes: {wf.get('last_failure_codes')}")
        lines.append(f"in_progress: {wf.get('current_state')}")
    else:
        lines.append(f"in_progress: {wf.get('current_state')}")
    inv = st.STATE_INVARIANTS.get(str(wf.get("current_state") or ""), "")
    if inv:
        lines.append(f"invariant: {inv}")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Explain Yonko workflow history")
    p.add_argument("--session", required=True)
    args = p.parse_args()
    sys.stdout.write(explain(Path(args.session)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
