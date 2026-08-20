"""Engineering Efficiency Report (V4 Phase 1) - observational only.

Fail-open: callers must catch exceptions. Never changes seating, packets,
prompts, retrieval, or verification behaviour.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 4  # rough estimate; not billing-accurate


def est_tokens(text: str | bytes | None) -> int:
    if text is None:
        return 0
    if isinstance(text, bytes):
        n = len(text)
    else:
        n = len(text.encode("utf-8"))
    return max(0, (n + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def split_sections(packet: str) -> dict[str, str]:
    """Split packet.md on === HEADER === lines."""
    if not packet:
        return {}
    parts = re.split(r"(?m)^(=== .+? ===)\s*$", packet)
    sections: dict[str, str] = {}
    i = 1
    while i + 1 < len(parts):
        header = parts[i].strip()
        body = parts[i + 1]
        name = header.strip("= ").strip()
        # merge duplicate headers
        if name in sections:
            sections[name] += body
        else:
            sections[name] = body
        i += 2
    if parts and parts[0].strip() and not sections:
        sections["preamble"] = parts[0]
    return sections


def find_repeated_paragraphs(text: str, min_chars: int = 120) -> list[dict[str, Any]]:
    """Exact-ish paragraph duplicates (normalized whitespace)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    norm_map: dict[str, list[str]] = {}
    for p in paras:
        if len(p) < min_chars:
            continue
        key = re.sub(r"\s+", " ", p).strip().lower()
        norm_map.setdefault(key, []).append(p[:80])
    out = []
    for key, samples in norm_map.items():
        if len(samples) < 2:
            continue
        out.append({
            "occurrences": len(samples),
            "chars": len(key),
            "estimated_tokens_each": est_tokens(key),
            "estimated_waste_tokens": est_tokens(key) * (len(samples) - 1),
            "preview": samples[0][:100],
        })
    out.sort(key=lambda x: -x["estimated_waste_tokens"])
    return out[:20]


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _workflow_section(session_dir: Path) -> dict[str, Any] | None:
    """V3.4 workflow metrics - absent on older sessions. Observational only."""
    wf = load_json(session_dir / "workflow.json")
    if not wf:
        return None
    codes: Counter = Counter()
    would_blocks = 0
    blocked = 0
    transitions = 0
    stale = 0
    human = 0
    budget = 0
    write_pol = 0
    overrides = 0
    invalidations = 0
    repins = 0
    corrected_retries = 0
    ep = session_dir / "workflow-events.jsonl"
    seen_fin = False
    if ep.exists():
        for line in ep.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            transitions += 1
            if ev.get("would_block"):
                would_blocks += 1
            if ev.get("blocked"):
                blocked += 1
            tr = ev.get("transition")
            if tr == "invalidate_packet":
                invalidations += 1
            if tr == "pin_packet" and not ev.get("blocked") and not ev.get("would_block"):
                repins += 1
            if tr == "human_override_legality":
                overrides += 1
            if tr == "finalize" and ev.get("allowed") and seen_fin:
                corrected_retries += 1
            if tr == "finalize" and (ev.get("blocked") or ev.get("would_block")):
                seen_fin = True
            for c in ev.get("failure_codes") or []:
                codes[c] += 1
                if c == "PACKET_STALE":
                    stale += 1
                if c == "HUMAN_APPROVAL_REQUIRED":
                    human += 1
                if c == "BUDGET_EXCEEDED":
                    budget += 1
                if c == "WRITE_POLICY_VIOLATION":
                    write_pol += 1
    clean = (
        (wf.get("would_block_count") or 0) == 0
        and (wf.get("blocked_count") or 0) == 0
        and (wf.get("current_state") == "FINALIZED")
    )
    return {
        "mode": wf.get("mode") or "unknown",
        "current_state": wf.get("current_state"),
        "would_block_count": wf.get("would_block_count") or would_blocks,
        "blocked_count": wf.get("blocked_count") or blocked,
        "override_count": wf.get("override_count") or overrides,
        "transition_events": transitions,
        "failure_code_counts": dict(codes),
        "stale_packet_events": stale,
        "invalidate_packet_events": invalidations,
        "pin_packet_events": repins,
        "human_approval_violations": human,
        "confirmation_budget_violations": budget,
        "write_policy_violations": write_pol,
        "corrected_and_retried_finalizes": corrected_retries,
        "completed_without_violations": clean,
        "seat_count": wf.get("seat_count"),
        "confirmation_rounds": wf.get("confirmation_rounds"),
        "note": "Observational workflow legality metrics - not a quality score",
    }


def build_efficiency_report(session_dir: Path) -> dict[str, Any]:
    session_dir = Path(session_dir)
    session = load_json(session_dir / "session.json") or {}
    metrics = load_json(session_dir / "metrics.json") or {}
    confidence = load_json(session_dir / "confidence.json") or {}
    packet_meta = load_json(session_dir / "packet.meta.json") or {}
    packet_path = session_dir / "packet.md"
    packet_text = packet_path.read_text(encoding="utf-8") if packet_path.exists() else ""

    review_type = session.get("review_type") or metrics.get("review_type") or "implementation"
    risk = session.get("risk") or metrics.get("risk")

    sections = split_sections(packet_text)
    section_stats = []
    for name, body in sections.items():
        section_stats.append({
            "section": name,
            "bytes": len(body.encode("utf-8")),
            "chars": len(body),
            "lines": body.count("\n") + (1 if body and not body.endswith("\n") else 0),
            "estimated_tokens": est_tokens(body),
        })
    section_stats.sort(key=lambda x: -x["estimated_tokens"])

    packet_tokens = est_tokens(packet_text)
    seats_invoked = []
    events = []
    ep = session_dir / "events.jsonl"
    if ep.exists():
        for line in ep.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for e in events:
        if e.get("type") == "reviewers_seated":
            seats = (e.get("data") or {}).get("seats") or (e.get("data") or {}).get("reviewers")
            if isinstance(seats, list):
                seats_invoked = [str(s).lower() for s in seats]
            elif isinstance((e.get("data") or {}).get("count"), int):
                # count only
                pass
    if not seats_invoked:
        # infer from findings
        for name in ("findings.json", "findings.raw.json"):
            data = load_json(session_dir / name)
            if not data:
                continue
            findings = []
            if isinstance(data, dict):
                for k in ("findings", "plan_findings", "document_findings"):
                    if isinstance(data.get(k), list):
                        findings = data[k]
                        break
            elif isinstance(data, list):
                findings = data
            seats_invoked = sorted({
                (f.get("reviewer") or f.get("seat") or "").lower()
                for f in findings if isinstance(f, dict) and (f.get("reviewer") or f.get("seat"))
            } - {""})
            break

    n_seats = len(seats_invoked) or int(metrics.get("task_calls") or 0) or 0
    # Prefer risk seating count from metrics if present
    if n_seats == 0 and metrics.get("task_calls"):
        n_seats = int(metrics["task_calls"])

    repeated = find_repeated_paragraphs(packet_text)
    waste = sum(r["estimated_waste_tokens"] for r in repeated)

    # Prompt size by seat (from skill prompts - static estimate of Task wrapper)
    skill_root = Path(__file__).resolve().parent.parent.parent
    prompt_sizes = {}
    for rel, key in (
        ("prompts/reviewers.md", "implementation_reviewers_md"),
        ("prompts/plan-reviewers.md", "plan_reviewers_md"),
        ("prompts/document-reviewers.md", "document_reviewers_md"),
    ):
        p = skill_root / rel
        if p.exists():
            t = p.read_text(encoding="utf-8")
            prompt_sizes[key] = {
                "bytes": len(t.encode("utf-8")),
                "estimated_tokens": est_tokens(t),
            }

    # Seat prompt ceremony estimate: use general Task prompt block size once × seats
    # (observational - actual Task may inject full packet separately)
    reviewers_md = skill_root / "prompts" / "reviewers.md"
    seat_prompt_est = 0
    if reviewers_md.exists():
        # approximate shared rules + task template without packet
        seat_prompt_est = est_tokens(reviewers_md.read_text(encoding="utf-8")[:4000])

    repeated_input_est = packet_tokens * max(n_seats, 1) + seat_prompt_est * max(n_seats, 1)

    dedupe_receipt = packet_meta.get("deduplication") or {}
    compression = packet_meta.get("compression") or {
        "status": "none" if not dedupe_receipt.get("replacements") else "deduplicated",
    }
    if dedupe_receipt.get("replacements"):
        compression = {
            "status": "deduplicated",
            "replacements": dedupe_receipt.get("replacements"),
            "bytes_saved_estimate": dedupe_receipt.get("bytes_saved_estimate"),
        }

    linked = session.get("linked_session")
    linked_artifacts = []
    if linked:
        linked_artifacts.append({"kind": "linked_session", "path": linked})
    if (session_dir / "evidence" / "approved-plan.md").exists():
        linked_artifacts.append({"kind": "approved_plan", "path": "evidence/approved-plan.md"})
    for name in ("PLAN.approved.md", "PAP.final.md", "PRD.final.md", "ADR.final.md", "DESIGN.final.md"):
        if (session_dir / name).exists():
            linked_artifacts.append({"kind": "session_artifact", "path": name})

    # Review metrics
    rematches = sum(1 for e in events if e.get("type") in ("rematch", "confirmation_round", "round_complete"))
    chair_rounds = int(session.get("round") or metrics.get("rounds") or 0)
    verifier_runs = sum(1 for e in events if e.get("type") == "verification_completed")
    scoped_verify = sum(1 for e in events if e.get("type") == "scoped_verify")

    findings = []
    for name in ("findings.json", "findings.raw.json"):
        data = load_json(session_dir / name)
        if not data:
            continue
        if isinstance(data, dict):
            for k in ("findings", "plan_findings", "document_findings"):
                if isinstance(data.get(k), list):
                    findings = data[k]
                    break
        elif isinstance(data, list):
            findings = data
        break

    material = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = (f.get("severity") or "").lower()
        if sev in ("high", "critical", "medium"):
            material += 1
        elif sev:
            material += 1  # count all defect findings as material candidates
        else:
            material += 1

    # Knowledge (best-effort from evidence index candidate / informed_by)
    knowledge = {
        "historical_evidence_retrieved": 0,
        "historical_evidence_referenced": 0,
        "candidate_eligible": None,
        "candidate_created": (session_dir / "evidence-candidate" / "record.json").exists(),
        "candidate_published": False,
        "relationships_recorded": 0,
        "concepts_indexed": [],
        "potential_duplicate_evidence": 0,
        "note": "Knowledge counts are best-effort from session artifacts; Evidence Index publish is separate.",
    }
    # informed_by in candidate
    cand = load_json(session_dir / "evidence-candidate" / "record.json")
    if isinstance(cand, dict):
        informed = cand.get("informed_by") or []
        knowledge["historical_evidence_referenced"] = len(informed) if isinstance(informed, list) else 0
        knowledge["relationships_recorded"] = len(cand.get("relationships") or [])
        knowledge["concepts_indexed"] = [
            c.get("value") for c in (cand.get("concepts") or []) if isinstance(c, dict) and c.get("value")
        ]
    # Docket / packet mention of historical evidence
    hist_hits = len(re.findall(r"(?i)informed_by|historical evidence|evidence_id", packet_text))
    knowledge["historical_evidence_retrieved"] = max(
        knowledge["historical_evidence_referenced"], hist_hits // 2
    )

    opportunities = []
    if waste > 0:
        opportunities.append({
            "kind": "repeated_paragraphs_in_packet",
            "estimated_tokens": waste,
            "note": "Exact/near-exact paragraph duplicates inside the packet",
        })
    if n_seats > 1 and seat_prompt_est > 800:
        opportunities.append({
            "kind": "seat_prompt_ceremony_x_seats",
            "estimated_tokens": seat_prompt_est * (n_seats - 1),
            "note": "Shared ceremony repeated per seat (structural; slim prompts reduce this)",
        })
    # Linked session noise heuristic
    if review_type == "implementation" and linked and "findings.json" in packet_text.lower():
        opportunities.append({
            "kind": "possible_plan_session_noise",
            "estimated_tokens": None,
            "note": "Packet text mentions findings.json - linked plan handoff should use PLAN.approved.md only",
        })

    report = {
        "schema_version": "1.0.0",
        "report_type": "engineering_efficiency",
        "observational_only": True,
        "disclaimer": (
            "Observational only. No seating, packet, prompt, retrieval or verification "
            "behaviour was changed from these metrics. Metrics inform humans; humans change Yonko. "
            "Token figures are estimates (chars/4), not billing-accurate."
        ),
        "session_id": session.get("session_id"),
        "review_type": review_type,
        "risk_band": risk,
        "packet": {
            "bytes": len(packet_text.encode("utf-8")),
            "chars": len(packet_text),
            "lines": packet_text.count("\n") + 1 if packet_text else 0,
            "estimated_tokens": packet_tokens,
            "sections": section_stats,
            "largest_sections": section_stats[:5],
            "repeated_material": repeated,
            "repeated_material_waste_tokens_est": waste,
            "reviewer_prompt_files": prompt_sizes,
            "estimated_seat_prompt_tokens_each": seat_prompt_est,
            "estimated_repeated_input_across_seats": repeated_input_est,
            "seats_for_repeat_estimate": n_seats,
            "linked_artifacts": linked_artifacts,
            "compression": compression,
            "deduplication_receipt": dedupe_receipt,
            "potential_structural_savings": opportunities,
            "relevant_context_note": "Full relevant context ≠ full available context",
        },
        "review": {
            "review_type": review_type,
            "risk_band": risk,
            "seats_invoked": seats_invoked,
            "seat_count": n_seats,
            "reviewer_rounds": rematches,
            "chair_rounds": chair_rounds,
            "verifier_runs": verifier_runs,
            "scoped_verify_runs": scoped_verify,
            "finding_count": len(findings),
            "material_findings_est": material,
            "validated_findings": metrics.get("verification", {}).get("confirmed")
            if isinstance(metrics.get("verification"), dict)
            else None,
            "rejected_findings": metrics.get("verification", {}).get("rejected")
            if isinstance(metrics.get("verification"), dict)
            else None,
            "final_confidence": confidence.get("level") or session.get("engineering_confidence"),
            "duration_seconds": metrics.get("duration_seconds"),
            "task_calls": metrics.get("task_calls"),
            "packet_bytes": metrics.get("packet_bytes") or packet_meta.get("bytes"),
        },
        "knowledge": knowledge,
        "workflow": _workflow_section(session_dir),
        "observational_summary": (
            "No optimisation performed. Metrics recorded only. "
            "Future optimisation decisions remain human-controlled."
        ),
    }
    return report


def format_efficiency_markdown(report: dict[str, Any]) -> str:
    pkt = report.get("packet") or {}
    rev = report.get("review") or {}
    know = report.get("knowledge") or {}
    lines = [
        "## Engineering Efficiency Report",
        "",
        report.get("disclaimer", ""),
        "",
        "### Packet",
        f"- Estimated tokens: {pkt.get('estimated_tokens')}",
        f"- Bytes / lines: {pkt.get('bytes')} / {pkt.get('lines')}",
        f"- Estimated repeated input across seats: {pkt.get('estimated_repeated_input_across_seats')}",
        f"- Compression: {(pkt.get('compression') or {}).get('status', 'none')}",
        f"- Repeated material waste (est.): {pkt.get('repeated_material_waste_tokens_est')} tokens",
        "- Largest sections:",
    ]
    for s in (pkt.get("largest_sections") or [])[:5]:
        lines.append(f"  - {s.get('section')}: ~{s.get('estimated_tokens')} tokens")
    if pkt.get("potential_structural_savings"):
        lines.append("- Potential structural savings (estimates, not applied):")
        for o in pkt["potential_structural_savings"]:
            tok = o.get("estimated_tokens")
            tok_s = f"~{tok} tokens" if tok is not None else "n/a"
            lines.append(f"  - {o.get('kind')}: {tok_s} - {o.get('note')}")
    lines.extend([
        "",
        "### Review",
        f"- Type / risk: {rev.get('review_type')} / {rev.get('risk_band')}",
        f"- Seats invoked: {', '.join(rev.get('seats_invoked') or []) or '(unknown)'} ({rev.get('seat_count')})",
        f"- Chair rounds / rematch-like events: {rev.get('chair_rounds')} / {rev.get('reviewer_rounds')}",
        f"- Verifier / scoped_verify runs: {rev.get('verifier_runs')} / {rev.get('scoped_verify_runs')}",
        f"- Findings (count / material est.): {rev.get('finding_count')} / {rev.get('material_findings_est')}",
        f"- Final confidence: {rev.get('final_confidence')}",
        f"- Duration (s): {rev.get('duration_seconds')}",
        "",
        "### Knowledge",
        f"- Historical evidence referenced: {know.get('historical_evidence_referenced')}",
        f"- Candidate created: {know.get('candidate_created')}",
        f"- Relationships recorded: {know.get('relationships_recorded')}",
        f"- Concepts: {', '.join(know.get('concepts_indexed') or []) or '(none in session candidate)'}",
        "",
    ])
    wf = report.get("workflow")
    if wf:
        lines.extend([
            "### Workflow (shadow)",
            f"- State: {wf.get('current_state')} (mode={wf.get('mode')})",
            f"- Transition events: {wf.get('transition_events')}",
            f"- Would-block count: {wf.get('would_block_count')}",
            f"- Failure codes: {wf.get('failure_code_counts') or '{}'}",
            f"- Note: {wf.get('note')}",
            "",
        ])
    lines.extend([
        "### Observational Summary",
        "",
        report.get("observational_summary", ""),
        "",
    ])
    return "\n".join(lines)


def write_efficiency_report(session_dir: Path) -> dict[str, Any]:
    """Write efficiency-report.json and append markdown to SUMMARY.md. Fail-open for callers."""
    session_dir = Path(session_dir)
    report = build_efficiency_report(session_dir)
    (session_dir / "efficiency-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    md = format_efficiency_markdown(report)
    summary = session_dir / "SUMMARY.md"
    if summary.exists():
        existing = summary.read_text(encoding="utf-8")
        if "## Engineering Efficiency Report" not in existing:
            summary.write_text(existing.rstrip() + "\n\n" + md, encoding="utf-8")
    else:
        summary.write_text(md, encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    r = write_efficiency_report(d)
    print(json.dumps({"ok": True, "estimated_packet_tokens": r["packet"]["estimated_tokens"]}, indent=2))
