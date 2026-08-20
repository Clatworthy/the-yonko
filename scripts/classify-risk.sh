#!/usr/bin/env bash
# classify-risk.sh - deterministic risk band + reasons from collected evidence.
# Usage: classify-risk.sh --session <dir> [--force trivial|low|medium|high|critical|quick|full]
# Implementation review only. Plan/document sessions use classify-scope-risk.sh.
# Writes evidence/risk.json and updates session.json.
# Prints risk JSON on stdout.

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
      echo "Usage: classify-risk.sh --session DIR [--force trivial|low|medium|high|critical|quick|full]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
REVIEW_TYPE="$(python3 -c "import json,pathlib,sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text()).get('review_type') or 'implementation')" "$SESSION/session.json")"
[[ "$REVIEW_TYPE" == "implementation" ]] || yonko_die "classify-risk.sh is implementation-only; use classify-scope-risk.sh for $REVIEW_TYPE sessions"
[[ -f "$SESSION/evidence/repos.json" ]] || yonko_die "run collect-evidence.sh first"

python3 - "$SESSION" "$YONKO_CONFIG/risk-policy.yaml" "$FORCE" <<'PY'
import json, pathlib, re, sys

session_dir = pathlib.Path(sys.argv[1])
policy_path = pathlib.Path(sys.argv[2])
force = (sys.argv[3] or "").strip().lower()

# Minimal YAML subset reader for our policy file (avoid PyYAML dependency)
def load_simple_yaml(text: str):
    # We only need bands + a few lists; parse with a tiny approach via JSON after
    # converting is fragile. Instead embed signal logic here and read bands via regex.
    return text

policy_text = policy_path.read_text(encoding="utf-8")

BAND_ORDER = ["trivial", "low", "medium", "high", "critical"]
band_rank = {b: i for i, b in enumerate(BAND_ORDER)}

repos = json.loads((session_dir / "evidence" / "repos.json").read_text(encoding="utf-8"))["repos"]
reasons = []
raised = "trivial"

def raise_to(band, reason):
    global raised
    reasons.append({"band": band, "reason": reason})
    if band_rank[band] > band_rank[raised]:
        raised = band

# Aggregate patch text + paths. Critical/high content signals ignore doc/help prose
# so articles that *mention* auth/billing do not raise those bands.
all_paths = []
total_lines = 0
has_prod = False
has_test = False
path_blob_parts = []
code_blob_parts = []
DOC_PATH = re.compile(
    r"(?i)(\.md$|\.mdx$|/docs/|/help/|README|CHANGELOG|CONTENT|\.txt$)"
)

def iter_file_hunks(text: str):
    """Yield (path, hunk_text) for each file section in a unified diff."""
    current_path = None
    buf = []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path is not None:
                yield current_path, "".join(buf)
            buf = [line]
            m = re.search(r" b/(.+)$", line.rstrip("\n"))
            current_path = m.group(1) if m else ""
        elif line.startswith("+++ b/"):
            current_path = line[6:].rstrip("\n")
            buf.append(line)
        else:
            buf.append(line)
    if current_path is not None:
        yield current_path, "".join(buf)

for r in repos:
    patch = session_dir / "evidence" / r["patch"]
    text = patch.read_text(encoding="utf-8", errors="replace") if patch.exists() else ""
    total_lines += sum(1 for line in text.splitlines() if line.startswith("+") or line.startswith("-"))
    for path, hunk in iter_file_hunks(text):
        all_paths.append(path)
        path_blob_parts.append(path)
        if re.search(r"(?i)(src/|main/)", path) and not re.search(r"(?i)test", path):
            has_prod = True
        if re.search(r"(?i)(test/|__tests__|\.test\.|\.spec\.)", path):
            has_test = True
        if DOC_PATH.search(path):
            continue
        code_blob_parts.append(hunk)

path_blob = "\n".join(path_blob_parts)
code_blob = "\n".join(code_blob_parts)
# Paths always count; code/content excludes documentation files.
blob = path_blob + "\n" + code_blob

# Critical signals (prefer identifiers / paths over bare English stems).
# Avoid: bare "auth" (author), "charge" (discharge), "ownership", "permission" in prose.
crit_patterns = [
    (
        "authorisation or auth middleware path changed",
        r"(?i)(@PreAuthorize|auth0|\boauth\b|authori[sz]ation|authentication|\brbac\b|\bjwt\b|"
        r"AuthMiddleware|permission.?check|features/auth|/auth/|middleware[^/\n]*auth|auth[^/\n]*middleware)",
    ),
    (
        "money / billing / invoice mutation path changed",
        # Letter-bounded money stems only (not org-specific flag names).
        # (?<![A-Za-z])invoice(?![A-Za-z]) matches invoice, InvoiceService, invoice_upload;
        # does not use bare "charge" (matches discharge).
        r"(?i)(\bbilling\b|(?<![A-Za-z])invoices?(?![A-Za-z])|\bpayments?\b|credit.?notes?|\bwallets?\b|\brefunds?\b)",
    ),
    (
        "customer isolation / tenancy boundary changed",
        r"(?i)(customer.?id|\btenant(?:Id|_id)?\b|\btenancy\b|multi.?tenant|data.?isolation|cross.?customer)",
    ),
    (
        "destructive data operation (hard/soft delete or cascade)",
        r"(?i)(hard.?delete|soft.?delete|\bCASCADE\b|ON DELETE CASCADE|drop (table|column)|truncate)",
    ),
]
for reason, pat in crit_patterns:
    if re.search(pat, blob):
        raise_to("critical", reason)

# High signals (avoid help URLs /api/, prose "migration", datePublished / content publish)
high_patterns = [
    (
        "public API / OpenAPI contract changed",
        r"(?i)(openapi|\bswagger\b|GraphQL|openapi-configurations|\*-model\b|api\.ya?ml)",
    ),
    (
        "database migration present",
        r"(?i)(flyway|liquibase|V[0-9]+__|/db/migration|schema\.sql)",
    ),
    (
        "SQS/SNS/async side-effect path changed",
        r"(?i)(\bsqs\b|\bsns\b|\bkafka\b|enqueue|\bwebhook\b|outbox|@SqsListener)",
    ),
]
for reason, pat in high_patterns:
    if re.search(pat, blob):
        raise_to("high", reason)

if len(repos) > 1:
    raise_to("high", "multiple repositories affected")

# Medium / low / trivial by size
if has_prod and has_test:
    raise_to("medium", "tests and production code both changed")
if total_lines >= 80:
    raise_to("medium", f"source change size >= 80 diff lines ({total_lines})")
elif total_lines >= 20:
    raise_to("low", f"small source change ({total_lines} diff lines)")
else:
    raise_to("trivial", f"docs/config-only or tiny diff ({total_lines} diff lines)")

# Force route mapping
forced_band = None
if force in BAND_ORDER:
    forced_band = force
elif force == "quick":
    forced_band = "low"
elif force == "full":
    # `full` is a floor, never a downgrade: it must not turn critical into high.
    forced_band = raised if band_rank[raised] > band_rank["high"] else "high"
elif force == "plan":
    raise SystemExit(
        "yonko: 'plan' is no longer an implementation route. Use --force full for the full "
        "council route, or run a plan-review session (init-session.sh --type plan)."
    )
elif force == "review":
    forced_band = raised
elif force:
    raise SystemExit(f"yonko: unknown --force value: {force}")

final = raised
safety_reasons = [r["reason"] for r in reasons if r["band"] in ("critical", "high")]
if forced_band is not None:
    # quick cannot bypass safety floor
    if force == "quick" and band_rank[raised] >= band_rank["high"]:
        final = raised
        reasons.append({"band": raised, "reason": "quick mode blocked by safety floor; keeping classifier band"})
    else:
        # allow force upward or downward except safety floor for quick handled above
        if band_rank[forced_band] >= band_rank[raised] or force == "full" or force in BAND_ORDER:
            # If forcing downward past high/critical safety, block when safety reasons present
            if band_rank[forced_band] < band_rank["high"] and any(r["band"] == "critical" for r in reasons):
                final = raised
                reasons.append({"band": raised, "reason": "force route cannot bypass critical safety signals"})
            else:
                final = forced_band
                reasons.append({"band": forced_band, "reason": f"forced route: {force or forced_band}"})

budgets = {
    "trivial": 1,
    "low": 2,
    "medium": 5,
    "high": 10,
    "critical": 12,
}
reviewers = {
    "trivial": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 4,
}

out = {
    "review_type": "implementation",
    "risk": final,
    "risk_basis": "diff-derived",
    "heuristic_raised": raised,
    "force": force or None,
    "reasons": [r["reason"] for r in reasons],
    "reason_details": reasons,
    "maximum_subagent_calls": budgets[final],
    "reviewers": reviewers[final],
    # V3: high/critical no longer runs an inline plan author/challenger.
    # It may only RECOMMEND that a separate `/yonko plan` session was warranted.
    "recommend_plan_review": final in ("high", "critical"),
    "verify_material": final in ("medium", "high", "critical"),
    "repo_count": len(repos),
    "diff_lines_approx": total_lines,
}

(session_dir / "evidence" / "risk.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
session["risk"] = final
session["risk_reasons"] = out["reasons"]
session["status"] = "risk_classified"
(session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

print(json.dumps(out, indent=2))
PY

RISK_DATA="$(python3 -c "import json,pathlib; r=json.loads(pathlib.Path('$SESSION/evidence/risk.json').read_text()); print(json.dumps({'risk': r['risk'], 'reviewers': r['reviewers'], 'maximum_subagent_calls': r['maximum_subagent_calls']}))")"
"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type risk_classified --data "$RISK_DATA"
