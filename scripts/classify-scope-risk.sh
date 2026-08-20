#!/usr/bin/env bash
# classify-scope-risk.sh - risk band for PLAN and DOCUMENT review (V3).
#
# HONESTY BOUNDARY: this is NOT diff-derived risk. It reads only stated scope and
# inspected context, so it can be fooled by omission. Output is labelled
# "heuristic from stated scope and inspected context" and reviewers are told to
# hunt omitted scope themselves.
#
# Usage: classify-scope-risk.sh --session DIR [--force trivial|low|medium|high|critical]
# Writes evidence/scope-risk.json and updates session.json. Prints JSON on stdout.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
FORCE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --force) FORCE="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: classify-scope-risk.sh --session DIR [--force trivial|low|medium|high|critical]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
if [[ ! -f "$SESSION/evidence/plan-refs.json" && ! -f "$SESSION/evidence/doc-refs.json" ]]; then
  yonko_die "run collect-plan-evidence.sh or collect-document-evidence.sh first"
fi

python3 - "$SESSION" "$FORCE" <<'PY'
import json, pathlib, re, sys

session_dir = pathlib.Path(sys.argv[1])
force = (sys.argv[2] or "").strip().lower()
evid = session_dir / "evidence"

BAND_ORDER = ["trivial", "low", "medium", "high", "critical"]
band_rank = {b: i for i, b in enumerate(BAND_ORDER)}

refs_path = evid / "plan-refs.json" if (evid / "plan-refs.json").exists() else evid / "doc-refs.json"
refs = json.loads(refs_path.read_text(encoding="utf-8"))
review_type = refs.get("review_type", "plan")
artifact_type = refs.get("artifact_type")

texts = []
for candidate in ("plan.md", "document.md", "recon.md"):
    p = evid / candidate
    if p.exists():
        texts.append(p.read_text(encoding="utf-8", errors="replace"))
src_dir = evid / "sources"
if src_dir.exists():
    for p in sorted(src_dir.glob("*")):
        if p.is_file():
            texts.append(p.read_text(encoding="utf-8", errors="replace"))

blob = "\n".join(texts)
words = len(blob.split())

named_repos = refs.get("repositories_named") or refs.get("repositories_inspected") or []
repo_count = len(named_repos)

reasons = []
raised = "trivial"

def raise_to(band, reason):
    global raised
    reasons.append({"band": band, "reason": reason})
    if band_rank[band] > band_rank[raised]:
        raised = band

# Prefer concrete risk language over stems that fire on docs/ops prose
# (Charge/ownership, demo tenant, content publish, CMS migration, CDN purge,
# Auth0/login howto copy that names the IdP without changing auth systems).
CRITICAL = [
    (
        "stated scope touches authorisation or authentication",
        # Do NOT match bare auth0 / authentication / login - help and product docs
        # name those constantly. Require engineering change surface.
        r"(?i)\b(rbac|permission checks?|scope[s]?\s+grant|"
        r"jwt (secret|validation|middleware|issuer)|"
        r"auth(?:0)?[- ]?(?:config|configuration|actions?|rules?|hooks?|middleware)|"
        r"(?:authori[sz]ation|authentication) (?:middleware|boundary|provider|system|service)|"
        r"(?:change|modify|replace|implement|migrate) (?:auth0|authorisation|authorization|authentication|rbac))\b",
    ),
    (
        "stated scope touches money, billing or invoicing",
        r"(?i)\b(billing|invoices?|payments?|credit.?notes?|wallets?|refunds?|tariff)\b",
    ),
    (
        "stated scope touches customer isolation or tenancy",
        r"(?i)\b(multi.?tenant|customer isolation|data isolation|cross.?customer|tenant.?id|tenant boundar|"
        r"tenancy boundar|customer.?tenancy)\b",
    ),
    (
        "stated scope includes destructive data operations",
        r"(?i)\b(hard.?delete|soft.?delete|drop (table|column)|truncate|on delete cascade|cascade delete)\b",
    ),
]
HIGH = [
    (
        "stated scope changes a public API or contract",
        r"(?i)\b(openapi|api contract|graphql schema|breaking change|api versioning)\b",
    ),
    (
        "stated scope includes a database migration",
        r"(?i)\b(flyway|liquibase|schema change|alter table|new column|database migration|db migration)\b",
    ),
    (
        "stated scope includes async or event side effects",
        r"(?i)\b(sqs|sns|kafka|webhook|outbox|event schema|message queue)\b",
    ),
    (
        "stated scope includes deployment or rollout coordination",
        r"(?i)\b(two.?phase deploy|deploy order|rollout|rollback|feature flag)\b",
    ),
]
MEDIUM = [
    (
        "stated scope spans multiple services or systems",
        r"(?i)\b(multi.?service|cross.?service|service boundar|microservices?|multi.?repo|distributed system)\b",
    ),
    (
        "stated scope mentions concurrency or retries",
        r"(?i)\b(concurren|race condition|idempoten|retry|lock|transaction boundary)\b",
    ),
]

for reason, pat in CRITICAL:
    if re.search(pat, blob):
        raise_to("critical", reason)
for reason, pat in HIGH:
    if re.search(pat, blob):
        raise_to("high", reason)
for reason, pat in MEDIUM:
    if re.search(pat, blob):
        raise_to("medium", reason)

if repo_count > 1:
    raise_to("high", f"plan names {repo_count} repositories")
elif repo_count == 1:
    raise_to("low", "plan names a single repository")

if words >= 1500:
    raise_to("medium", f"large artifact ({words} words)")
elif words >= 400:
    raise_to("low", f"moderate artifact ({words} words)")
else:
    raise_to("trivial", f"small artifact ({words} words)")

# Mechanically true, deliberately weak: term presence only. NOT a finding.
COVERAGE_TERMS = {
    "migration": r"(?i)\bmigrat",
    "rollout": r"(?i)\broll ?out\b|\brollout\b",
    "rollback": r"(?i)\broll ?back\b|\brollback\b",
    "deploy_order": r"(?i)deploy(ment)? order|deploy first|two.?phase",
    "testing": r"(?i)\btest(s|ing)?\b",
    "observability": r"(?i)\b(metric|log|alarm|trace|observab)",
    "failure_modes": r"(?i)\b(failure mode|timeout|partial failure|degrad)",
    "compatibility": r"(?i)\b(backward|compatib|consumer)",
    "ownership": r"(?i)\b(owner|ownership|responsible team)\b",
}
terms_not_present = sorted(k for k, pat in COVERAGE_TERMS.items() if not re.search(pat, blob))

final = raised
forced = None
if force:
    if force not in BAND_ORDER:
        raise SystemExit(f"yonko: unknown --force value for scope risk: {force}")
    forced = force
    if band_rank[force] < band_rank["high"] and any(r["band"] == "critical" for r in reasons):
        reasons.append({"band": raised, "reason": "force cannot bypass critical scope signals"})
    else:
        final = force
        reasons.append({"band": force, "reason": f"forced band: {force}"})

reviewers = {"trivial": 2, "low": 2, "medium": 3, "high": 4, "critical": 4}
budgets = {"trivial": 2, "low": 3, "medium": 4, "high": 6, "critical": 7}

out = {
    "review_type": review_type,
    "artifact_type": artifact_type,
    "risk": final,
    "risk_basis": "heuristic from stated scope and inspected context",
    "not_equivalent_to_diff_risk": True,
    "heuristic_raised": raised,
    "force": forced,
    "reasons": [r["reason"] for r in reasons],
    "reason_details": reasons,
    "reviewers": reviewers[final],
    "maximum_subagent_calls": budgets[final],
    "max_confirmation_rounds": 1,
    "verify_material": final in ("medium", "high", "critical"),
    "repositories_referenced": repo_count,
    "words": words,
    "terms_not_present": terms_not_present,
    "terms_not_present_note": "Term absence only. Reviewers must judge whether the step is genuinely missing.",
    "omission_hunt_required": True,
    "omission_note": "Scope regexes see stated intent only. Absent scope is a reviewer duty, not a classifier output.",
}

(evid / "scope-risk.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
session["risk"] = final
session["risk_basis"] = out["risk_basis"]
session["risk_reasons"] = out["reasons"]
session["status"] = "risk_classified"
(session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

print(json.dumps(out, indent=2))
PY

RISK_DATA="$(python3 -c "import json,pathlib; r=json.loads(pathlib.Path('$SESSION/evidence/scope-risk.json').read_text()); print(json.dumps({'risk': r['risk'], 'risk_basis': r['risk_basis'], 'reviewers': r['reviewers'], 'maximum_subagent_calls': r['maximum_subagent_calls']}))")"
"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type scope_risk_classified --data "$RISK_DATA"
