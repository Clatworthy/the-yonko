#!/usr/bin/env bash
# validate-artifact.sh - structural schema validation for findings / verification / verdict JSON.
# Usage:
#   validate-artifact.sh --kind KIND --file <path>
# KIND:
#   finding | findings                     implementation findings (V2, unchanged)
#   plan-finding | plan-findings           plan-review findings (V3)
#   document-finding | document-findings   document-review findings (V3)
#   verification | verdict
# Exit 0 on pass, 1 on fail. Prints JSON report on stdout.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

KIND=""
FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kind) KIND="${2:-}"; shift 2 ;;
    --file) FILE="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: validate-artifact.sh --kind finding|findings|plan-finding|plan-findings|document-finding|document-findings|verification|verdict|review-measurement|council-effectiveness|eval-case|eval-run --file PATH"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

[[ -n "$KIND" && -n "$FILE" ]] || yonko_die "--kind and --file required"
[[ -f "$FILE" ]] || yonko_die "file not found: $FILE"

# Resolve session dir if the artefact lives in a Yonko session (shadow observe only)
WORKFLOW_SESSION=""
_abs="$(cd "$(dirname "$FILE")" && pwd)/$(basename "$FILE")"
_dir="$(cd "$(dirname "$FILE")" && pwd)"
if [[ -f "$_dir/session.json" ]]; then
  WORKFLOW_SESSION="$_dir"
elif [[ -f "$(dirname "$_dir")/session.json" ]]; then
  WORKFLOW_SESSION="$(cd "$(dirname "$_dir")" && pwd)"
fi

set +e
python3 - "$KIND" "$FILE" "$YONKO_CONTRACTS" <<'PY'
import json, pathlib, sys

kind, path, contracts = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
data = json.loads(path.read_text(encoding="utf-8"))

# Models often emit disposition casing variants; canonicalize Remand|Content in place.
if isinstance(data, dict) and isinstance(data.get("disposition"), str):
    canon = {"remand": "Remand", "content": "Content"}.get(data["disposition"].strip().lower())
    if canon and data["disposition"] != canon:
        data["disposition"] = canon
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

UNGROUNDED = ("n/a", "na", "none", "vibes", "tbd", "unknown", "-", "see above")
SEVERITIES = ("critical", "high", "medium", "low")
CONFIDENCES = ("low", "medium", "high")

PLAN_CATEGORIES = (
    "missing-repository", "missing-contract", "architectural-assumption", "migration",
    "rollout", "rollback", "deploy-order", "concurrency-failure-mode", "compatibility",
    "testing-strategy", "unnecessary-complexity", "ownership-decision", "security", "other",
)
PLAN_EVIDENCE_KINDS = ("plan_section", "code_inspected", "contract_inspected", "document_inspected")

DOC_CATEGORIES = (
    "inaccurate-claim", "unsupported-claim", "missing-section", "ambiguous-requirement",
    "unresolved-decision", "internal-contradiction", "implementation-risk",
    "operational-gap", "missing-stakeholder-concern", "other",
)
DOC_EVIDENCE_KINDS = (
    "document_section", "source_material", "code_inspected",
    "contract_inspected", "document_inspected",
)


def common_checks(f, prefix, problems):
    if f.get("confidence") not in (None,) + CONFIDENCES:
        problems.append(f"{prefix}: confidence must be low|medium|high")
    if isinstance(f.get("confidence"), (int, float)):
        problems.append(f"{prefix}: numeric confidence forbidden")
    if f.get("severity") not in (None,) + SEVERITIES:
        problems.append(f"{prefix}: invalid severity")


def validate_finding(f, idx=None):
    prefix = f"findings[{idx}]" if idx is not None else "finding"
    if not isinstance(f, dict):
        return [f"{prefix}: not an object"]
    problems = []
    for field in ("id", "reviewer", "category", "severity", "title", "claim", "evidence", "reachability", "impact", "confidence"):
        if field not in f or f[field] in (None, ""):
            problems.append(f"{prefix}: missing {field}")
    locus = f.get("locus")
    if not isinstance(locus, dict):
        problems.append(f"{prefix}: locus must be object with repository+path")
    else:
        if not locus.get("repository") or not locus.get("path"):
            problems.append(f"{prefix}: locus.repository and locus.path required")
    common_checks(f, prefix, problems)
    ev = str(f.get("evidence") or "")
    if ev.strip().lower() in ("n/a", "none", "vibes", "tbd"):
        problems.append(f"{prefix}: evidence is not concrete")
    return problems


def validate_plan_finding(f, idx=None):
    prefix = f"plan_findings[{idx}]" if idx is not None else "plan_finding"
    if not isinstance(f, dict):
        return [f"{prefix}: not an object"]
    problems = []
    for field in ("id", "reviewer", "category", "severity", "title", "claim",
                  "evidence_kind", "evidence_reference", "production_consequence", "confidence"):
        if field not in f or f[field] in (None, ""):
            problems.append(f"{prefix}: missing {field}")
    common_checks(f, prefix, problems)
    if f.get("category") not in (None,) + PLAN_CATEGORIES:
        problems.append(f"{prefix}: invalid category for plan review")
    if f.get("evidence_kind") not in (None,) + PLAN_EVIDENCE_KINDS:
        problems.append(f"{prefix}: evidence_kind must be one of {'|'.join(PLAN_EVIDENCE_KINDS)}")
    ref = str(f.get("evidence_reference") or "").strip()
    if ref.lower() in UNGROUNDED:
        problems.append(f"{prefix}: evidence_reference is not concrete")
    if f.get("evidence_kind") == "code_inspected" and ref and "/" not in ref:
        problems.append(f"{prefix}: code_inspected evidence_reference must include a repository path")
    if f.get("category") in ("missing-repository", "missing-contract") and not f.get("missing_element"):
        problems.append(f"{prefix}: missing_element required for {f.get('category')}")
    if f.get("category") == "architectural-assumption" and not f.get("assumption_challenged"):
        problems.append(f"{prefix}: assumption_challenged required for architectural-assumption")
    locus = f.get("locus")
    if locus is not None:
        if not isinstance(locus, dict):
            problems.append(f"{prefix}: locus must be an object when present")
        elif not locus.get("repository") or not locus.get("path"):
            problems.append(f"{prefix}: locus requires repository and path when present")
    return problems


def validate_document_finding(f, idx=None):
    prefix = f"document_findings[{idx}]" if idx is not None else "document_finding"
    if not isinstance(f, dict):
        return [f"{prefix}: not an object"]
    problems = []
    for field in ("id", "reviewer", "category", "severity", "title", "claim",
                  "evidence_kind", "evidence_reference", "impact", "confidence"):
        if field not in f or f[field] in (None, ""):
            problems.append(f"{prefix}: missing {field}")
    common_checks(f, prefix, problems)
    if f.get("category") not in (None,) + DOC_CATEGORIES:
        problems.append(f"{prefix}: invalid category for document review")
    if f.get("evidence_kind") not in (None,) + DOC_EVIDENCE_KINDS:
        problems.append(f"{prefix}: evidence_kind must be one of {'|'.join(DOC_EVIDENCE_KINDS)}")
    ref = str(f.get("evidence_reference") or "").strip()
    if ref.lower() in UNGROUNDED:
        problems.append(f"{prefix}: evidence_reference is not concrete")
    if f.get("evidence_kind") == "code_inspected" and ref and "/" not in ref:
        problems.append(f"{prefix}: code_inspected evidence_reference must include a repository path")
    if f.get("category") == "missing-section" and not (f.get("missing_section") or f.get("section")):
        problems.append(f"{prefix}: missing_section or section required for missing-section")
    if f.get("category") in ("inaccurate-claim", "internal-contradiction") and not f.get("section"):
        problems.append(f"{prefix}: section required for {f.get('category')}")
    return problems


def validate_array(raw, key, validator):
    if isinstance(raw, dict) and key in raw:
        arr = raw[key]
    elif isinstance(raw, dict) and "findings" in raw:
        arr = raw["findings"]
    else:
        arr = raw
    if not isinstance(arr, list):
        return [f"{key} must be a JSON array (or {{{key}: [...]}})"]
    out = []
    for i, f in enumerate(arr):
        out.extend(validator(f, i))
    return out


problems = []

if kind == "finding":
    problems = validate_finding(data)
elif kind == "findings":
    problems = validate_array(data, "findings", validate_finding)
    # Envelope contract (deliberate): top-level disposition is required for seat output.
    # Chair three-axis outcome remains authoritative; seat disposition is Remand|Content only.
    if isinstance(data, dict):
        disp = data.get("disposition")
        if disp not in ("Remand", "Content"):
            problems.append("disposition must be Remand|Content")
        findings_arr = data.get("findings") if isinstance(data.get("findings"), list) else []
        if findings_arr and disp == "Content":
            problems.append("disposition Content with non-empty findings; use Remand")
        conf = data.get("confidence")
        if conf is not None and conf not in CONFIDENCES:
            problems.append("top-level confidence must be low|medium|high when present")
elif kind == "plan-finding":
    problems = validate_plan_finding(data)
elif kind == "plan-findings":
    problems = validate_array(data, "plan_findings", validate_plan_finding)
elif kind == "document-finding":
    problems = validate_document_finding(data)
elif kind == "document-findings":
    problems = validate_array(data, "document_findings", validate_document_finding)
elif kind == "verification":
    if not isinstance(data, dict):
        problems = ["verification must be an object"]
    else:
        for field in ("finding_ids", "verdict", "evidence", "verifier"):
            if field not in data or data[field] in (None, "", []):
                problems.append(f"missing {field}")
        if data.get("verdict") not in (None, "confirmed", "rejected", "inconclusive"):
            problems.append("invalid verdict")
elif kind == "verdict":
    if not isinstance(data, dict):
        problems = ["verdict must be an object"]
    else:
        for field in ("session_id", "round", "verdict", "risk", "mode"):
            if field not in data or data[field] in (None, ""):
                problems.append(f"missing {field}")
        if data.get("verdict") not in (None, "pass", "remand", "deadlock", "adjourned"):
            problems.append("invalid verdict")
        rt = data.get("review_type")
        if rt not in (None, "implementation", "plan", "document"):
            problems.append("review_type must be implementation|plan|document")
        if rt == "document" and not data.get("artifact_type"):
            problems.append("document verdict requires artifact_type")
elif kind == "review-measurement":
    if not isinstance(data, dict):
        problems = ["review-measurement must be an object"]
    else:
        if data.get("schema_version") != 1:
            problems.append("schema_version must be 1")
        for field in ("session_id", "adjudication_state"):
            if field not in data or data[field] in (None, ""):
                problems.append(f"missing {field}")
        allowed_states = {
            "complete", "partial", "pre_adjudication",
            "plan_array_form", "document_array_form", "empty_findings",
        }
        if data.get("adjudication_state") not in (None, *allowed_states):
            problems.append("invalid adjudication_state")
elif kind == "council-effectiveness":
    if not isinstance(data, dict):
        problems = ["council-effectiveness must be an object"]
    else:
        if data.get("schema_version") != 1:
            problems.append("schema_version must be 1")
        for field in ("session_id", "totals", "seats"):
            if field not in data:
                problems.append(f"missing {field}")
        if "insufficient_sample" in data and not isinstance(data.get("insufficient_sample"), bool):
            problems.append("insufficient_sample must be boolean when present")
elif kind == "eval-case":
    if not isinstance(data, dict):
        problems = ["eval-case must be an object"]
    else:
        if data.get("schema_version") != 1:
            problems.append("schema_version must be 1")
        for field in ("case_id", "source_session_id", "packet_hash", "approved_by"):
            if field not in data or data[field] in (None, ""):
                problems.append(f"missing {field}")
elif kind == "eval-run":
    if not isinstance(data, dict):
        problems = ["eval-run must be an object"]
    else:
        if data.get("schema_version") != 1:
            problems.append("schema_version must be 1")
        for field in ("run_id", "case_id", "replay_mode"):
            if field not in data or data[field] in (None, ""):
                problems.append(f"missing {field}")
        if data.get("replay_mode") not in (None, "frozen_packet", "full_pipeline"):
            problems.append("replay_mode must be frozen_packet|full_pipeline")
else:
    problems = [f"unknown kind: {kind}"]

# Cross-check schema file exists (documentation of contract)
schema_map = {
    "finding": "finding.schema.json",
    "findings": "finding.schema.json",
    "plan-finding": "plan-finding.schema.json",
    "plan-findings": "plan-finding.schema.json",
    "document-finding": "document-finding.schema.json",
    "document-findings": "document-finding.schema.json",
    "verification": "verification.schema.json",
    "verdict": "verdict.schema.json",
    "review-measurement": "evaluation/review-measurement.schema.json",
    "council-effectiveness": "evaluation/council-effectiveness.schema.json",
    "eval-case": "evaluation/eval-case.schema.json",
    "eval-run": "evaluation/eval-run.schema.json",
}
schema = contracts / schema_map.get(kind, "")
if schema_map.get(kind) and not schema.exists():
    problems.append(f"schema file missing: {schema}")

report = {"ok": len(problems) == 0, "kind": kind, "file": str(path), "problems": problems}
print(json.dumps(report, indent=2))
sys.exit(0 if report["ok"] else 1)
PY

_rc=$?
set -e
case "$KIND" in
  finding|findings|plan-finding|plan-findings|document-finding|document-findings)
    if [[ $_rc -eq 0 && -n "$WORKFLOW_SESSION" ]]; then
      yonko_workflow_observe "$WORKFLOW_SESSION" "validate_findings" "{\"kind\":\"$KIND\",\"ok\":true}"
    fi
    ;;
esac
exit "$_rc"
