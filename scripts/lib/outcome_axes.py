"""Three-axis end-of-run outcome model (Yonko Evidence Graph v1+).

Separates defect review from evidence completeness from ship advice.

Never collapse "no validated defects" into PASS/FAIL alone.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

CLEAN_PASS_BLOCKING_CATEGORIES = frozenset(
    {
        "cross_repository_consumers",
        "operational_side_effects",
    }
)

FORBIDDEN_SOLE_CLEAN_LABELS = (
    "pass",
    "clean",
    "clean pass",
    "push-ready",
    "ready",
    "ready to push",
    "safe to merge",
)


def load_graph_completeness(session_dir: pathlib.Path) -> dict[str, Any] | None:
    p = session_dir / "evidence" / "graph-completeness.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def derive_evidence_completeness(completeness: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Return (complete|incomplete, reasons)."""
    if completeness is None:
        return "incomplete", ["graph-completeness.json absent"]
    reasons: list[str] = []
    if completeness.get("blocks_complete_verdict"):
        reasons.append("blocks_complete_verdict=true")
    for row in completeness.get("categories") or []:
        if row.get("status") == "unresolved":
            reasons.append(f"category unresolved: {row.get('category')}")
    for u in completeness.get("unresolved_edges_material") or []:
        cat = u.get("category") or u.get("relationship") or "edge"
        reasons.append(f"material unresolved: {cat}")
    # Also treat any required unresolved edge as incomplete even if not in material list
    # (categories already cover most cases)
    if reasons:
        # Deduplicate preserve order
        seen: set[str] = set()
        uniq = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                uniq.append(r)
        return "incomplete", uniq
    # All covered / not_applicable
    statuses = {row.get("status") for row in (completeness.get("categories") or [])}
    if statuses and statuses <= {"covered", "not_applicable"}:
        return "complete", []
    return "incomplete", ["categories not all covered/not_applicable"]


def unresolved_blocking_categories(completeness: dict[str, Any] | None) -> list[str]:
    if not completeness:
        return sorted(CLEAN_PASS_BLOCKING_CATEGORIES)
    found: list[str] = []
    for row in completeness.get("categories") or []:
        cat = row.get("category")
        if cat in CLEAN_PASS_BLOCKING_CATEGORIES and row.get("status") == "unresolved":
            found.append(str(cat))
    for u in completeness.get("unresolved_edges_material") or []:
        cat = u.get("category")
        if cat in CLEAN_PASS_BLOCKING_CATEGORIES and cat not in found:
            found.append(str(cat))
    return found


def derive_review_outcome(
    legacy_verdict: str,
    findings_total: int = 0,
    open_material_findings: int | None = None,
) -> str:
    """pass | findings | inconclusive - defect axis only."""
    open_n = open_material_findings if open_material_findings is not None else findings_total
    if legacy_verdict in ("deadlock", "adjourned"):
        return "inconclusive"
    if legacy_verdict == "remand" or open_n > 0:
        return "findings"
    if legacy_verdict == "pass":
        return "pass"
    return "inconclusive"


def derive_deployment_recommendation(
    review_outcome: str,
    evidence_completeness: str,
    *,
    blocks_seating: bool = False,
) -> str:
    """proceed | proceed_with_caveat | block."""
    if review_outcome == "findings":
        return "block"
    if review_outcome == "inconclusive":
        return "block"
    if blocks_seating:
        return "block"
    if review_outcome == "pass" and evidence_completeness == "complete":
        return "proceed"
    if review_outcome == "pass" and evidence_completeness == "incomplete":
        return "proceed_with_caveat"
    return "block"


def derive_clean_pass_allowed(
    review_outcome: str,
    evidence_completeness: str,
    deployment: str,
    blocking_categories: list[str],
) -> bool:
    return (
        review_outcome == "pass"
        and evidence_completeness == "complete"
        and deployment == "proceed"
        and not blocking_categories
    )


def derive_presentation(
    *,
    review_outcome: str,
    evidence_completeness: str,
    deployment: str,
    clean_pass_allowed: bool,
    blocking_categories: list[str],
    incomplete_reasons: list[str],
) -> dict[str, Any]:
    if clean_pass_allowed:
        headline = "Pass"
    elif review_outcome == "pass" and evidence_completeness == "incomplete":
        cats = blocking_categories or [
            r.split(":")[-1].strip()
            for r in incomplete_reasons
            if "unresolved" in r or "category" in r
        ]
        named = ", ".join(cats[:4]) if cats else "evidence incomplete"
        headline = f"Pass with unresolved evidence ({named})"
    elif review_outcome == "findings":
        headline = "Findings remain"
    else:
        headline = "Review inconclusive"

    return {
        "headline": headline,
        "clean_pass_allowed": clean_pass_allowed,
        "forbidden_sole_labels": list(FORBIDDEN_SOLE_CLEAN_LABELS),
        "blocking_categories": blocking_categories,
        "note": (
            "Never headline sole Pass / push-ready / clean when evidence is incomplete "
            "for operational_side_effects or cross_repository_consumers."
        ),
    }


def render_final_verdict_block(axes: dict[str, Any]) -> str:
    """Human-facing verdict block that never claims a clean Pass when incomplete."""
    presentation = axes.get("presentation") or {}
    headline = presentation.get("headline") or axes.get("review_outcome") or "unknown"
    clean = bool(axes.get("clean_pass_allowed"))
    lines = [
        f"Headline: {headline}",
        f"Review outcome (defects): {axes.get('review_outcome')}",
        f"Evidence completeness: {axes.get('evidence_completeness')}",
        f"Deployment recommendation: {axes.get('deployment_recommendation')}",
        f"Clean pass allowed: {clean}",
    ]
    blocking = presentation.get("blocking_categories") or []
    if blocking:
        lines.append("Unresolved blocking categories: " + ", ".join(blocking))
    if not clean and axes.get("review_outcome") == "pass":
        lines.append(
            "Report as pass with unresolved evidence only. "
            "Forbidden sole labels when incomplete: push-ready, ready-to-push, "
            "safe-to-merge, clean-pass."
        )
    for reason in (axes.get("incomplete_reasons") or [])[:8]:
        lines.append(f"- unresolved: {reason}")
    return "\n".join(lines) + "\n"


def build_outcome_axes(
    session_dir: pathlib.Path,
    *,
    legacy_verdict: str,
    findings_total: int = 0,
    open_material_findings: int | None = None,
) -> dict[str, Any]:
    completeness = load_graph_completeness(session_dir)
    evidence_completeness, incomplete_reasons = derive_evidence_completeness(completeness)
    review_outcome = derive_review_outcome(
        legacy_verdict, findings_total=findings_total, open_material_findings=open_material_findings
    )
    blocks_seating = bool(completeness.get("blocks_seating")) if completeness else False
    deployment = derive_deployment_recommendation(
        review_outcome, evidence_completeness, blocks_seating=blocks_seating
    )
    blocking = unresolved_blocking_categories(completeness)
    # Incomplete evidence always blocks clean pass when ops/consumers unresolved,
    # and also when evidence is incomplete for any reason on a defect-free pass.
    if review_outcome == "pass" and evidence_completeness == "incomplete" and not blocking:
        # Still forbid clean pass; headline uses incomplete reasons.
        pass
    clean_pass_allowed = derive_clean_pass_allowed(
        review_outcome, evidence_completeness, deployment, blocking
    )
    if review_outcome == "pass" and evidence_completeness == "incomplete":
        clean_pass_allowed = False

    # Human-facing labels (never collapse to sole PASS/FAIL)
    review_label = {
        "pass": "No validated defects found",
        "findings": "Validated defects remain",
        "inconclusive": "Review inconclusive",
    }[review_outcome]
    evidence_label = {
        "complete": "Complete",
        "incomplete": "Incomplete",
    }[evidence_completeness]
    if incomplete_reasons:
        # Prefer consumer/cross-repo wording when present
        primary = incomplete_reasons[0]
        for r in incomplete_reasons:
            if "cross_repository" in r or "external" in r or "consumers" in r:
                primary = r
                break
        if "cross_repository" in primary or "external" in primary or "consumers" in primary:
            evidence_label = "Incomplete - external consumers unresolved"
        elif "operational_side_effects" in primary:
            evidence_label = "Incomplete - operational side effects unresolved"
        else:
            evidence_label = f"Incomplete - {primary}"

    deploy_label = {
        "proceed": "Proceed",
        "proceed_with_caveat": "Proceed with caveat",
        "block": "Block",
    }[deployment]

    presentation = derive_presentation(
        review_outcome=review_outcome,
        evidence_completeness=evidence_completeness,
        deployment=deployment,
        clean_pass_allowed=clean_pass_allowed,
        blocking_categories=blocking,
        incomplete_reasons=incomplete_reasons,
    )

    # Confidence ceiling: never suggest HIGH when evidence incomplete on a "pass"
    confidence_ceiling = None
    if review_outcome == "pass" and evidence_completeness == "incomplete":
        confidence_ceiling = "medium"
    if review_outcome in ("findings", "inconclusive"):
        confidence_ceiling = "low" if review_outcome == "inconclusive" else "medium"

    return {
        "schema_version": "1",
        "legacy_verdict": legacy_verdict,
        "review_outcome": review_outcome,
        "evidence_completeness": evidence_completeness,
        "deployment_recommendation": deployment,
        "clean_pass_allowed": clean_pass_allowed,
        "presentation": presentation,
        "labels": {
            "review_outcome": review_label,
            "evidence_completeness": evidence_label,
            "deployment_recommendation": deploy_label,
            "headline": presentation["headline"],
        },
        "incomplete_reasons": incomplete_reasons,
        "blocks_seating": blocks_seating,
        "blocks_complete_verdict": bool(completeness.get("blocks_complete_verdict"))
        if completeness
        else False,
        "confidence_ceiling": confidence_ceiling,
        "note": (
            "review_outcome is about validated defects only. "
            "evidence_completeness is independent. "
            "Never treat review_outcome=pass as system-wide safety when evidence is incomplete. "
            "Never claim push-ready / clean pass when clean_pass_allowed is false."
        ),
    }
