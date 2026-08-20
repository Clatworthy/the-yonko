"""Review-type-specific path quality. Empty findings → not_applicable, never vacuous pass."""

from __future__ import annotations

from typing import Any


def _present(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    if isinstance(val, (list, dict)) and not val:
        return False
    return True


def _locus_ok(f: dict[str, Any]) -> bool:
    locus = f.get("locus")
    if isinstance(locus, dict):
        return _present(locus.get("path") or locus.get("repository") or locus.get("file"))
    return _present(locus)


def check_finding_path_quality(f: dict[str, Any], review_type: str) -> dict[str, Any]:
    rt = (review_type or "implementation").lower()
    missing: list[str] = []
    n_a: list[str] = []

    if rt == "plan":
        for field in ("evidence_kind", "evidence_reference", "production_consequence"):
            if not _present(f.get(field)):
                missing.append(field)
        if not _locus_ok(f):
            n_a.append("locus_optional")
    elif rt == "document":
        for field in ("evidence_kind", "evidence_reference", "impact"):
            if not _present(f.get(field)):
                missing.append(field)
    else:  # implementation
        if not _locus_ok(f):
            missing.append("locus")
        for field in ("evidence", "reachability", "impact"):
            if not _present(f.get(field)):
                missing.append(field)

    status = "pass" if not missing else "fail"
    return {
        "finding_id": f.get("id"),
        "status": status,
        "missing_fields": missing,
        "n_a_fields": n_a,
    }


def assess_path_quality(
    *,
    review_type: str,
    findings: list[dict[str, Any]],
    seats_completed: bool,
) -> dict[str, Any]:
    """Aggregate path quality. Empty findings never get a vacuous pass."""
    if not findings:
        return {
            "status": "not_applicable",
            "reason": "empty_findings",
            "review_type": review_type,
            "seats_completed": seats_completed,
            "findings_checked": 0,
            "pass_count": 0,
            "fail_count": 0,
            "details": [],
            "vacuous_pass": False,
        }

    details = [check_finding_path_quality(f, review_type) for f in findings]
    fails = [d for d in details if d["status"] == "fail"]
    passes = [d for d in details if d["status"] == "pass"]
    status = "pass" if not fails else "fail"
    return {
        "status": status,
        "reason": None,
        "review_type": review_type,
        "seats_completed": seats_completed,
        "findings_checked": len(details),
        "pass_count": len(passes),
        "fail_count": len(fails),
        "details": details,
        "vacuous_pass": False,
    }
