#!/usr/bin/env bash
# finalize-session.sh - observational end-of-run metrics, confidence, SUMMARY.md (V2.1, V3-aware)
# Does NOT change seating, adjudication, or apply behaviour.
# Works for implementation, plan and document sessions (read from session.json review_type).
#
# Usage:
#   finalize-session.sh --session DIR --verdict pass|remand|deadlock|adjourned \
#     [--confidence high|medium|low] [--reason TEXT]... \
#     [--chair-note TEXT]
#
# Writes: metrics.json, confidence.json, SUMMARY.md
# Records event: session_finalized

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

SESSION=""
VERDICT=""
CONFIDENCE=""
CHAIR_NOTE=""
REASONS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --verdict) VERDICT="${2:-}"; shift 2 ;;
    --confidence) CONFIDENCE="${2:-}"; shift 2 ;;
    --reason) REASONS+=("${2:-}"); shift 2 ;;
    --chair-note) CHAIR_NOTE="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: finalize-session.sh --session DIR --verdict pass|remand|deadlock|adjourned [--confidence high|medium|low] [--reason TEXT]... [--chair-note TEXT]"
      exit 0
      ;;
    *) yonko_die "unknown arg: $1" ;;
  esac
done

yonko_require_session "$SESSION"
[[ -n "$VERDICT" ]] || yonko_die "--verdict required"
case "$VERDICT" in
  pass|remand|deadlock|adjourned) ;;
  *) yonko_die "verdict must be pass|remand|deadlock|adjourned" ;;
esac
if [[ -n "$CONFIDENCE" ]]; then
  case "$CONFIDENCE" in
    high|medium|low) ;;
    *) yonko_die "confidence must be high|medium|low" ;;
  esac
fi

# Pass reasons via RS-separated env (NUL is illegal in env vars)
export YONKO_FINALIZE_REASONS="$(
  if [[ ${#REASONS[@]} -gt 0 ]]; then
    printf '%s\036' "${REASONS[@]}"
  fi
)"
export YONKO_FINALIZE_CHAIR_NOTE="$CHAIR_NOTE"
export YONKO_FINALIZE_CONFIDENCE="$CONFIDENCE"
export YONKO_FINALIZE_VERDICT="$VERDICT"
export YONKO_SCRIPTS_DIR="$SCRIPT_DIR"

python3 - "$SESSION" <<'PY'
import json, os, pathlib, datetime, re, sys
from collections import Counter, defaultdict

session_dir = pathlib.Path(sys.argv[1])
verdict = os.environ.get("YONKO_FINALIZE_VERDICT", "")
confidence_in = (os.environ.get("YONKO_FINALIZE_CONFIDENCE") or "").strip().lower() or None
chair_note = os.environ.get("YONKO_FINALIZE_CHAIR_NOTE") or ""
reasons_raw = os.environ.get("YONKO_FINALIZE_REASONS") or ""
chair_reasons = [r for r in reasons_raw.split("\036") if r]

session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
review_type = session.get("review_type") or "implementation"
artifact_type = session.get("artifact_type")
document_mode = session.get("document_mode")
linked_session = session.get("linked_session")

# V3.4: evaluate finalize guards before writing finalized artefacts (enforce fail-closed)
try:
    _scripts = pathlib.Path(os.environ.get("YONKO_SCRIPTS_DIR") or str(pathlib.Path.home() / ".cursor/skills/the-yonko/scripts"))
    if str(_scripts / "workflow") not in sys.path:
        sys.path.insert(0, str(_scripts / "workflow"))
    import transition as _tr  # noqa: E402
    import guards as _g  # noqa: E402
    import config as _c  # noqa: E402
    import state as _st  # noqa: E402
    _prior = _st.load_workflow(session_dir) if _st.workflow_path(session_dir).exists() else None
    _mode = _c.resolve_mode(_prior)
    _wfstate = _st.load_workflow(session_dir, default_mode=_mode)
    _allowed, _codes = _g.evaluate(session_dir, "finalize", _wfstate, {"verdict": verdict})
    if not _allowed and _mode == "enforce":
        _blocked = _tr.record_transition(session_dir, "finalize", {"verdict": verdict}, None)
        print(json.dumps({
            "ok": False,
            "blocked": True,
            "failure_codes": _blocked.get("failure_codes") or _codes,
            "remediation": _blocked.get("remediation"),
            "mode": "enforce",
            "message": "finalize blocked by workflow legality - session not finalized",
        }, indent=2), file=sys.stderr)
        raise SystemExit(2)
except SystemExit:
    raise
except Exception as _gate_err:
    # Fail-open for gate implementation failures (not confirmed violations)
    try:
        (session_dir / "workflow.error.txt").write_text(f"finalize gate: {_gate_err}\n", encoding="utf-8")
    except Exception:
        pass

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

def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None

times = [parse_ts(e.get("ts")) for e in events]
times = [t for t in times if t]
started = None
sa = session.get("started_at")
if isinstance(sa, str):
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            started = datetime.datetime.strptime(sa, fmt)
            break
        except ValueError:
            pass
ended = times[-1] if times else datetime.datetime.utcnow()
if started is None and times:
    started = times[0]
duration_s = int((ended - started).total_seconds()) if started and ended else None

# Findings
findings = []
FINDING_KEYS = ("findings", "plan_findings", "document_findings")
for name in ("findings.json", "findings.raw.json"):
    p = session_dir / name
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in FINDING_KEYS:
                    if isinstance(data.get(key), list):
                        findings = data[key]
                        break
            elif isinstance(data, list):
                findings = data
            break
        except json.JSONDecodeError:
            pass

for e in events:
    if e.get("type") == "findings_merged":
        arr = (e.get("data") or {}).get("findings")
        if isinstance(arr, list) and arr:
            findings = arr

by_seat = Counter()
unique_titles_by_seat = defaultdict(set)
for f in findings:
    if not isinstance(f, dict):
        continue
    seat = (f.get("reviewer") or f.get("seat") or "unknown").lower()
    title = f.get("title") or f.get("id") or ""
    by_seat[seat] += 1
    if title:
        unique_titles_by_seat[seat].add(title)

unique_by_seat = {k: len(v) for k, v in unique_titles_by_seat.items()}

ver_confirmed = ver_rejected = ver_inconclusive = 0
for name in ("verification.json", "verifications.json"):
    p = session_dir / name
    if not p.exists():
        continue
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    items = data if isinstance(data, list) else data.get("verifications") or ([data] if isinstance(data, dict) and "verdict" in data else [])
    for item in items:
        if not isinstance(item, dict):
            continue
        v = item.get("verdict")
        if v == "confirmed":
            ver_confirmed += 1
        elif v == "rejected":
            ver_rejected += 1
        elif v == "inconclusive":
            ver_inconclusive += 1

for e in events:
    if e.get("type") != "verification_completed":
        continue
    d = e.get("data") or {}
    ver_confirmed += int(d.get("confirmed") or 0)
    ver_rejected += int(d.get("rejected") or 0)
    ver_inconclusive += int(d.get("inconclusive") or 0)
    if d.get("verdict") == "confirmed":
        ver_confirmed += 1
    elif d.get("verdict") == "rejected":
        ver_rejected += 1
    elif d.get("verdict") == "inconclusive":
        ver_inconclusive += 1

ver_total = ver_confirmed + ver_rejected + ver_inconclusive
reject_rate = round(100.0 * ver_rejected / ver_total, 1) if ver_total else None

task_calls = int(session.get("subagent_calls") or 0)
models_invoked = []
for e in events:
    if e.get("type") != "task_call":
        continue
    d = e.get("data") or {}
    task_calls += int(d.get("count") or 1)
    model = d.get("model") or d.get("model_slug") or d.get("model_hint")
    if isinstance(model, str) and model.strip():
        models_invoked.append(model.strip())

# Seat budget = routed seats (plan), not actual Task invocations.
seat_budget = None
routing_path = session_dir / "evidence" / "routing.json"
if routing_path.exists():
    try:
        rj_route = json.loads(routing_path.read_text(encoding="utf-8"))
        seats = rj_route.get("seats")
        if isinstance(seats, list):
            seat_budget = len(seats)
        elif isinstance(seats, dict):
            seat_budget = len(seats)
    except json.JSONDecodeError:
        pass

risk = session.get("risk")
risk_basis = session.get("risk_basis") or ("diff-derived" if review_type == "implementation" else "heuristic from stated scope and inspected context")
budget = None
risk_path = session_dir / "evidence" / "risk.json"
if not risk_path.exists():
    risk_path = session_dir / "evidence" / "scope-risk.json"
if risk_path.exists():
    try:
        rj = json.loads(risk_path.read_text(encoding="utf-8"))
        risk = risk or rj.get("risk")
        risk_basis = rj.get("risk_basis") or risk_basis
        budget = rj.get("maximum_subagent_calls")
        if seat_budget is None and rj.get("reviewers") is not None:
            seat_budget = int(rj.get("reviewers"))
    except json.JSONDecodeError:
        pass

force_override = session.get("force_route") or None
if risk_path.exists():
    try:
        rj_force = json.loads(risk_path.read_text(encoding="utf-8"))
        if rj_force.get("force"):
            force_override = rj_force.get("force")
    except json.JSONDecodeError:
        pass

rounds = int(session.get("round") or 0)
for e in events:
    if e.get("type") == "round_complete":
        rounds = max(rounds, int(e.get("round") or 0))

packet_hash = session.get("packet_hash")
packet_version = session.get("packet_version")
packet_bytes = None
packet_meta = session_dir / "packet.meta.json"
if packet_meta.exists():
    try:
        pm = json.loads(packet_meta.read_text(encoding="utf-8"))
        packet_hash = packet_hash or pm.get("packet_hash")
        packet_version = packet_version or pm.get("packet_version")
        packet_bytes = pm.get("bytes")
    except json.JSONDecodeError:
        pass
if packet_bytes is None and (session_dir / "packet.md").exists():
    packet_bytes = (session_dir / "packet.md").stat().st_size

def tick(ok):
    return "yes" if ok else "no"

packet_complete = bool(packet_hash)
risk_reviewed = bool(risk) or risk_path.exists()
route = risk or "unknown"
verify_n_a = route in ("trivial", "low")
verification_ok = verify_n_a or ver_total > 0 or any(e.get("type") == "verification_completed" for e in events)
if route in ("medium", "high", "critical") and not (ver_total > 0 or any(e.get("type") == "verification_completed" for e in events)):
    verification_status = "unknown" if not verify_n_a else "n/a"
elif verify_n_a:
    verification_status = "n/a"
elif verification_ok:
    verification_status = "yes"
else:
    verification_status = "no"

scoped = [e for e in events if e.get("type") == "scoped_verify"]
det_status = "unknown"
if review_type != "implementation":
    # Plan and document review never run scoped production tests.
    det_status = "n/a"
elif scoped:
    results = [(e.get("data") or {}).get("result") for e in scoped]
    if any(r == "red" for r in results):
        det_status = "no"
    elif any(r in ("green", "skipped") for r in results):
        det_status = "yes"
elif any(e.get("type") == "deterministic_check" for e in events):
    det_status = "yes"

HANDOFF_FILES = {
    "plan": ["PLAN.approved.md", "PLAN.revised.md"],
    "document": {
        "pap": ["PAP.final.md"],
        "prd": ["PRD.final.md"],
        "adr": ["ADR.final.md"],
        "design": ["DESIGN.final.md"],
    },
}
handoff_status = "n/a"
handoff_path = None
if review_type == "plan":
    candidates = HANDOFF_FILES["plan"]
elif review_type == "document":
    candidates = HANDOFF_FILES["document"].get(artifact_type or "", [])
else:
    candidates = []
if candidates:
    found = [c for c in candidates if (session_dir / c).exists()]
    handoff_status = "yes" if found else "no"
    handoff_path = str(session_dir / found[0]) if found else None

# Evidence completeness per review type (mechanical file presence only).
evid = session_dir / "evidence"
premature_create = False
if review_type == "implementation":
    evidence_status = tick((evid / "repos.json").exists())
elif review_type == "plan":
    evidence_status = tick((evid / "plan-refs.json").exists() and (evid / "plan.md").exists())
else:
    refs_path = evid / "doc-refs.json"
    refs_ok = refs_path.exists()
    # Latest evidence state wins: a create session that has drafted flips to review mode.
    latest_mode = document_mode
    if refs_ok:
        try:
            latest_mode = json.loads(refs_path.read_text(encoding="utf-8")).get("mode") or document_mode
        except json.JSONDecodeError:
            pass
    draft_ok = latest_mode == "create" or (evid / "document.md").exists()
    evidence_status = tick(refs_ok and draft_ok)
    # Nothing drafted yet: the council cannot have reviewed a real artifact.
    premature_create = latest_mode == "create"

mechanical = {
    "packet_complete": tick(packet_complete),
    "evidence_collected": evidence_status,
    "risk_reviewed": tick(risk_reviewed),
    "risk_basis": risk_basis,
    "verification_complete_or_n_a": verification_status,
    "deterministic_checks_recorded": det_status,
    "handoff_artifact_present": handoff_status,
}

suggested = "medium"
if (verdict == "pass" and packet_complete and risk_reviewed
        and evidence_status == "yes"
        and verification_status in ("yes", "n/a")
        and det_status in ("yes", "n/a", "unknown")
        and handoff_status in ("yes", "n/a")):
    suggested = "high"
if verdict == "deadlock" or det_status == "no" or not packet_complete or evidence_status == "no":
    suggested = "low"
if verdict == "remand":
    suggested = "medium"
if verdict == "pass" and handoff_status == "no":
    suggested = "medium"
if premature_create:
    suggested = "low"

# Three-axis outcome (defects vs evidence vs ship advice) - never collapse to sole PASS/FAIL
outcome_axes = {
    "schema_version": "1",
    "legacy_verdict": verdict,
    "review_outcome": "inconclusive",
    "evidence_completeness": "incomplete",
    "deployment_recommendation": "block",
    "labels": {},
    "incomplete_reasons": ["outcome_axes unavailable"],
    "note": "fallback",
}
try:
    import importlib.util
    _scripts_root = pathlib.Path(
        os.environ.get("YONKO_SCRIPTS_DIR")
        or str(pathlib.Path.home() / ".cursor/skills/the-yonko/scripts")
    )
    _oa_path = _scripts_root / "lib" / "outcome_axes.py"
    _spec = importlib.util.spec_from_file_location("yonko_outcome_axes", _oa_path)
    _oa = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_oa)
    outcome_axes = _oa.build_outcome_axes(
        session_dir,
        legacy_verdict=verdict,
        findings_total=len(findings),
    )
except Exception as _e:
    outcome_axes["note"] = f"outcome_axes_error: {_e}"

# Cap suggested confidence when evidence incomplete on a defect-free pass
if outcome_axes.get("confidence_ceiling") == "medium" and suggested == "high":
    suggested = "medium"
    chair_reasons = list(chair_reasons) + ["evidence completeness incomplete - confidence capped at medium"]
if outcome_axes.get("confidence_ceiling") == "low" and suggested in ("high", "medium"):
    suggested = "low"

_rank = {"low": 0, "medium": 1, "high": 2}
confidence = confidence_in or suggested
if confidence_in and outcome_axes.get("confidence_ceiling"):
    ceiling = outcome_axes["confidence_ceiling"]
    if _rank.get(confidence_in, 0) > _rank.get(ceiling, 0):
        confidence = ceiling
        chair_reasons = list(chair_reasons) + [
            f"chair confidence {confidence_in} clamped to ceiling {ceiling}"
        ]
confidence_source = "chair" if confidence_in else "suggested_from_mechanical"
if confidence_in and confidence != confidence_in:
    confidence_source = "chair_clamped_to_ceiling"

def mark(status, yes=("yes",), na=("n/a",)):
    if status in yes:
        return "✓"
    if status in na:
        return "-"
    if status == "unknown":
        return "?"
    return "✗"

checks_md = [
    f"- {mark(mechanical['packet_complete'])} packet complete",
    f"- {mark(evidence_status)} evidence collected for {review_type} review",
    f"- {mark(mechanical['risk_reviewed'])} risk reviewed ({risk_basis})",
    f"- {mark(verification_status)} verification complete (or n/a for route)",
    f"- {mark(det_status)} deterministic checks passed / recorded",
]
if handoff_status != "n/a":
    checks_md.append(f"- {mark(handoff_status)} handoff artifact written")
for r in chair_reasons:
    checks_md.append(f"- ✓ {r}")
if outcome_axes.get("evidence_completeness") == "incomplete":
    checks_md.append("- ✗ evidence graph completeness incomplete (not equivalent to review failure)")
    for r in (outcome_axes.get("incomplete_reasons") or [])[:5]:
        checks_md.append(f"-   unresolved: {r}")
elif outcome_axes.get("evidence_completeness") == "complete":
    checks_md.append("- ✓ evidence graph completeness complete")
if chair_note:
    checks_md.append(f"- note: {chair_note}")

confidence_doc = {
    "level": confidence,
    "source": confidence_source,
    "suggested_level": suggested,
    "mechanical": mechanical,
    "chair_reasons": chair_reasons,
    "chair_note": chair_note or None,
    "policy": "observational_only_never_auto_tune",
    "outcome_axes": outcome_axes,
}

packet_text = ""
packet_file = session_dir / "packet.md"
if packet_file.is_file():
    packet_text = packet_file.read_text(encoding="utf-8", errors="replace")
exploration_ledgers = []
graph_gap_suggestions = []
runtime_dir = session_dir / "runtime"
if runtime_dir.is_dir():
    for seat_dir in sorted(runtime_dir.iterdir()):
        ledger_path = seat_dir / "repository-exploration.json"
        if not ledger_path.is_file():
            continue
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        exploration_ledgers.append({"seat": seat_dir.name, **ledger})
        for item in ledger.get("filesRead") or []:
            path = str(item.get("path") or "")
            if path and path not in packet_text:
                graph_gap_suggestions.append({
                    "seat": seat_dir.name,
                    "path": path,
                    "classification": "packet_omission_candidate",
                    "action": "suggest_evidence_graph_eval_case",
                })
discovery_summary = {
    "schema_version": 1,
    "policy": "suggest_only_never_auto_tune",
    "seats": exploration_ledgers,
    "graph_gap_suggestions": graph_gap_suggestions,
    "miss_taxonomy": [
        "model_missed_available_packet_evidence",
        "packet_omission",
        "repository_unavailable",
        "reviewer_did_not_explore",
        "routing_or_lens_mismatch",
    ],
    "budget_truncated": any(
        bool(row.get("truncated")) for row in exploration_ledgers
    ),
}
evidence_dir = session_dir / "evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)
(evidence_dir / "repository-exploration-summary.json").write_text(
    json.dumps(discovery_summary, indent=2) + "\n",
    encoding="utf-8",
)

metrics = {
    "version": "3.0.0",
    "session_id": session.get("session_id"),
    "review_type": review_type,
    "artifact_type": artifact_type,
    "document_mode": document_mode,
    "linked_session": linked_session,
    "verdict": verdict,
    "mode": session.get("mode"),
    "risk": risk,
    "risk_basis": risk_basis,
    "force": force_override,
    "seat_budget": seat_budget,
    "route_budget": budget,
    "rounds": rounds,
    "duration_seconds": duration_s,
    "task_calls": task_calls,
    "models_invoked": models_invoked,
    "packet_hash": packet_hash,
    "packet_version": packet_version,
    "packet_bytes": packet_bytes,
    "findings_total": len(findings),
    "findings_by_seat": dict(by_seat),
    "unique_findings_by_seat": unique_by_seat,
    "verification": {
        "confirmed": ver_confirmed,
        "rejected": ver_rejected,
        "inconclusive": ver_inconclusive,
        "reject_rate_percent": reject_rate,
    },
    "applies": sum(1 for e in events if e.get("type") == "apply"),
    "revisions": sum(1 for e in events if e.get("type") in ("artifact_revised", "plan_revised", "document_revised")),
    "handoff_artifact": handoff_path,
    "engineering_confidence": confidence,
    "review_outcome": outcome_axes.get("review_outcome"),
    "evidence_completeness": outcome_axes.get("evidence_completeness"),
    "deployment_recommendation": outcome_axes.get("deployment_recommendation"),
    "outcome_axes": outcome_axes,
    "repository_exploration": {
        "seats": len(exploration_ledgers),
        "graph_gap_suggestions": len(graph_gap_suggestions),
        "budget_truncated": discovery_summary["budget_truncated"],
    },
    "policy": "learning_only_never_auto_tune",
}

# Observational execution snapshot only - never feeds routing or model choice.
exec_profile = session.get("execution_profile") or {}
runtime_seats = []
runtime_dir = session_dir / "runtime"
if runtime_dir.is_dir():
    for seat_dir in sorted(runtime_dir.iterdir()):
        if not seat_dir.is_dir():
            continue
        rp = seat_dir / "result.json"
        if not rp.is_file():
            continue
        try:
            rr = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runtime_seats.append(
            {
                "seat": rr.get("seat") or seat_dir.name,
                "runtime": rr.get("runtime"),
                "model": rr.get("model_actual") or rr.get("model_configured"),
                "model_configured": rr.get("model_configured"),
                "model_actual": rr.get("model_actual"),
                "completed": bool(rr.get("completed")),
                "awaiting_chair_dispatch": bool(rr.get("awaiting_chair_dispatch")),
                "skipped_by_routing": bool(rr.get("skipped_by_routing")),
                "schema_valid": bool(rr.get("schema_valid")),
                "timeout": bool(rr.get("timeout")),
                "attempts": rr.get("attempts"),
                "duration_ms": rr.get("duration_ms"),
                "exit_status": rr.get("exit_status"),
                "failure_category": rr.get("failure_category"),
                "fallback_occurred": bool(rr.get("fallback_occurred")),
                "output_path": rr.get("output_path"),
                "started_at": rr.get("started_at"),
                "ended_at": rr.get("ended_at"),
            }
        )
if not runtime_seats and isinstance(exec_profile.get("seats"), list):
    for row in exec_profile["seats"]:
        runtime_seats.append(
            {
                "seat": row.get("seat"),
                "runtime": row.get("runtime"),
                "model": row.get("model"),
                "completed": False,
                "note": "frozen_mapping_only",
            }
        )

execution = {
    "schema_version": "1.1",
    "policy": "observational_only_never_influences_routing",
    "session": session.get("session_id"),
    "reviewType": review_type,
    "band": risk,
    "force": force_override,
    "seatBudget": seat_budget,
    "subagentCalls": task_calls,
    "models": models_invoked,
    "executionProfile": exec_profile.get("executionProfile"),
    "profileFingerprint": exec_profile.get("profile_fingerprint"),
    "seats": runtime_seats,
    "completed": True,
    "durationSec": duration_s,
    "verdict": verdict,
}
evid_dir = session_dir / "evidence"
evid_dir.mkdir(parents=True, exist_ok=True)
(evid_dir / "execution.json").write_text(json.dumps(execution, indent=2) + "\n", encoding="utf-8")

(session_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
(session_dir / "confidence.json").write_text(json.dumps(confidence_doc, indent=2) + "\n", encoding="utf-8")
(session_dir / "outcome.json").write_text(json.dumps(outcome_axes, indent=2) + "\n", encoding="utf-8")

# Observational evaluation capture (3.9.0) then ledger projection.
# Order: metrics/confidence/outcome (above) → capture → evaluation/* → index → ledger → SUMMARY.
review_quality_path = None
evaluation_paths = {}
try:
    _rq_root = pathlib.Path(
        os.environ.get("YONKO_SESSIONS_ROOT")
        or str(pathlib.Path.home() / ".cursor" / "yonko-sessions")
    )
    if str(_scripts_root) not in sys.path:
        sys.path.insert(0, str(_scripts_root))
    from lib.evaluation.capture import capture_or_fail_open  # noqa: E402
    from lib.review_quality_ledger import upsert_row, write_rollup  # noqa: E402

    _cap = capture_or_fail_open(session_dir, sessions_root_override=_rq_root)
    if _cap.get("ok") and _cap.get("ledger_row"):
        review_quality_path = str(upsert_row(_rq_root, _cap["ledger_row"]))
        write_rollup(_rq_root)
        evaluation_paths = _cap.get("paths") or {}
    elif _cap.get("skipped"):
        from lib.review_quality_ledger import build_row  # noqa: E402
        _rq_row = build_row(session_dir)
        review_quality_path = str(upsert_row(_rq_root, _rq_row))
        write_rollup(_rq_root)
    elif _cap.get("fail_open"):
        # Capture failed open: still attempt legacy ledger so rollup stays usable.
        from lib.review_quality_ledger import build_row  # noqa: E402
        _rq_row = build_row(session_dir)
        review_quality_path = str(upsert_row(_rq_root, _rq_row))
        write_rollup(_rq_root)
except Exception as _rq_err:
    _obs = {}
    try:
        from lib.evaluation.config import load_observability_evaluation  # noqa: E402
        _obs = load_observability_evaluation()
    except Exception:
        _obs = {"fail_open": True}
    try:
        (session_dir / "review-quality.error.txt").write_text(
            f"review_quality_ledger: {_rq_err}\n", encoding="utf-8"
        )
    except Exception:
        pass
    try:
        _eval_err = session_dir / "evaluation"
        _eval_err.mkdir(parents=True, exist_ok=True)
        (_eval_err / "capture.error.txt").write_text(
            f"finalize evaluation hook: {_rq_err}\n", encoding="utf-8"
        )
    except Exception:
        pass
    if _obs.get("fail_open") is False:
        raise

def fmt_dur(sec):
    if sec is None:
        return "unknown"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"

seats_line = ", ".join(
    f"{k} {unique_by_seat.get(k, by_seat.get(k, 0))}"
    for k in ("shanks", "blackbeard", "buggy", "luffy")
) or "none recorded"

ver_line = (
    f"confirmed {ver_confirmed} / rejected {ver_rejected} / inconclusive {ver_inconclusive}"
    + (f" (reject rate {reject_rate}%)" if reject_rate is not None else " (no verifier data)")
)

type_label = review_type if not artifact_type else f"{review_type} ({artifact_type}{'/' + document_mode if document_mode else ''})"
extra_lines = []
if handoff_status != "n/a":
    extra_lines.append(f"- Handoff artifact: {handoff_path or 'MISSING'}")
if linked_session:
    extra_lines.append(f"- Linked session: {linked_session}")
if review_type == "implementation":
    extra_lines.append(f"- Applies: {metrics['applies']}")
else:
    extra_lines.append(f"- Artifact revisions: {metrics['revisions']} (no production code changed)")

summary = f"""# Yonko session summary

- Session: {session.get('session_id')}
- Review type: {type_label}
- Headline: {outcome_axes.get('presentation', {}).get('headline') or outcome_axes.get('labels', {}).get('headline') or outcome_axes.get('review_outcome')}
- Clean pass allowed: {outcome_axes.get('clean_pass_allowed')}
- Legacy protocol verdict: {verdict}
- Review outcome: {outcome_axes.get('review_outcome')} - {outcome_axes.get('labels', {}).get('review_outcome', '')}
- Evidence completeness: {outcome_axes.get('evidence_completeness')} - {outcome_axes.get('labels', {}).get('evidence_completeness', '')}
- Deployment recommendation: {outcome_axes.get('deployment_recommendation')} - {outcome_axes.get('labels', {}).get('deployment_recommendation', '')}
- Mode / risk: {session.get('mode')} / {risk or 'unknown'} ({risk_basis})
- Duration: {fmt_dur(duration_s)}
- Rounds: {rounds}
- Task calls (actual): {task_calls} / seat budget {seat_budget if seat_budget is not None else 'n/a'} / call budget {budget if budget is not None else 'n/a'}
- Models invoked: {', '.join(models_invoked) if models_invoked else 'none recorded'}
- Packet: v{packet_version or '?'} / {(packet_hash or 'none')[:12]} / {packet_bytes if packet_bytes is not None else '?'} bytes
- Unique findings by seat: {seats_line}
- Verifier: {ver_line}
{chr(10).join(extra_lines)}
- Policy: metrics and evidence/execution.json are observational only (never auto-tune)

## Engineering Confidence

**{confidence.upper()}** ({confidence_source})

because:
{chr(10).join(checks_md)}

## Human runway

{(outcome_axes.get('presentation') or {}).get('headline') or ''}
{"Do not claim push-ready / clean pass - evidence incomplete." if not outcome_axes.get("clean_pass_allowed") else "Clean pass allowed only if chair agrees with complete evidence."}
(Chair prints full runway in chat; do not commit/push from Yonko.)
"""

(session_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")

# V4 Phase 1: Engineering Efficiency Report (observational, fail-open)
try:
    import importlib.util
    scripts_dir = pathlib.Path(os.environ.get("YONKO_SCRIPTS_DIR") or str(pathlib.Path.home() / ".cursor/skills/the-yonko/scripts"))
    mod_path = scripts_dir / "lib" / "efficiency_report.py"
    if mod_path.exists():
        spec = importlib.util.spec_from_file_location("yonko_efficiency_report", mod_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        mod.write_efficiency_report(session_dir)
except Exception as _eff_err:
    try:
        (session_dir / "efficiency-report.error.txt").write_text(str(_eff_err) + "\n", encoding="utf-8")
    except Exception:
        pass

session["status"] = "finalized"
session["verdict"] = verdict
session["engineering_confidence"] = confidence
session["finalized_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
(session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

print(json.dumps({
    "ok": True,
    "summary": str(session_dir / "SUMMARY.md"),
    "metrics": str(session_dir / "metrics.json"),
    "execution": str(evid_dir / "execution.json"),
    "review_quality": str(session_dir / "review-quality.json"),
    "review_quality_ledger": review_quality_path,
    "evaluation": evaluation_paths,
    "confidence": confidence,
    "suggested_confidence": suggested,
}, indent=2))

# Write event payload for the shell wrapper (avoids nested-quote breakage)
# Human approval must come from workflow/approve.py (explicit --approved-by), not Chair inference.

(session_dir / ".finalize-event.json").write_text(json.dumps({
    "verdict": verdict,
    "confidence": confidence,
    "review_type": review_type,
    "artifact_type": artifact_type,
    "task_calls": task_calls,
    "duration_seconds": duration_s,
}), encoding="utf-8")
PY

EVENT_DATA="$(cat "$SESSION/.finalize-event.json")"
"$SCRIPT_DIR/record-event.sh" --session "$SESSION" --type session_finalized --data "$EVENT_DATA"
rm -f "$SESSION/.finalize-event.json"
yonko_info "wrote $SESSION/SUMMARY.md (observational only)"

# Evidence Index: eligibility report only (never publishes; never git commit/push).
if [[ -x "$SCRIPT_DIR/evidence-index.py" || -f "$SCRIPT_DIR/evidence-index.py" ]]; then
  python3 - "$SESSION" <<'EI' || true
import json, os, pathlib, sys
session_dir = pathlib.Path(sys.argv[1])
session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
rt = session.get("review_type") or "implementation"
at = session.get("artifact_type")
handoff = {
  "plan": ["PLAN.approved.md"],
  "document": {
    "pap": ["PAP.final.md"], "prd": ["PRD.final.md"],
    "adr": ["ADR.final.md"], "design": ["DESIGN.final.md"],
  },
}
eligible = True
reasons = []
if session.get("status") != "finalized" and not (session_dir / "SUMMARY.md").exists():
    eligible = False
    reasons.append("session not finalized")
if rt == "plan":
    arts = handoff["plan"]
elif rt == "document":
    arts = handoff["document"].get(at or "", [])
else:
    arts = ["final.patch"] if (session_dir / "final.patch").exists() else []
    if not arts:
        evid = session_dir / "evidence" / "repos.json"
        if evid.exists():
            arts = ["evidence patches"]
        else:
            eligible = False
            reasons.append("no implementation patch evidence")
if rt in ("plan", "document"):
    if not any((session_dir / a).exists() for a in arts):
        eligible = False
        reasons.append(f"missing handoff artifact ({arts})")
repo = (os.environ.get("YONKO_EVIDENCE_REPO") or "").strip()
print(json.dumps({
  "evidence_index": {
    "eligible_for_candidate": eligible,
    "review_type": rt,
    "artifact_type": at,
    "YONKO_EVIDENCE_REPO": "set" if repo else "unset",
    "reasons": reasons,
    "next_steps": [
      "scripts/evidence-index.py candidate --session <dir> --owner <you> --final-status <status> --ticket <TA-...>",
      "scripts/evidence-index.py publish-local --session <dir> --candidate-hash <sha> --approved-by <you>",
    ] if eligible else [],
    "note": "Eligibility only. finalize-session never publishes evidence.",
  }
}, indent=2))
EI
fi
