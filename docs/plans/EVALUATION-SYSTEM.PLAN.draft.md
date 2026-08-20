# PLAN: Yonko Evaluation and Council Effectiveness (3.9.0)

**Status:** draft for `/yonko plan`  
**Skill root:** `~/.cursor/skills/the-yonko/`  
**Current VERSION:** 3.8.2  
**Proposed VERSION after implement:** **3.9.0** (new capability surface; not a 3.8.x patch)

## Goal

Add an observational evaluation layer that captures measurement at finalize, scores council effectiveness, builds a human-gated eval corpus, supports frozen-packet and full-pipeline replay, attaches ground truth / escaped defects, and proposes improvements **without** auto-applying them.

## Non-goals (hard)

- Do not change Packet hashing, Evidence Graph, cross-repo resolution, risk/routing, seat counts, finding/verifier/adjudicator semantics, or three-axis outcome semantics.
- Do not change `config/model-selections.json` or execution profiles in this change (Blackbeard Flash vs Pro is out of scope here; agreed panel may be edited separately by a human).
- No auto-tune, auto-commit/push, CI gate, or prompt/model auto-rewrite.
- No implementation-phase subagents; no LLM grading on finalize; no hosted analytics.

## Grounded inventory (existing SoT)

| Existing | Role | Reuse |
|----------|------|-------|
| `scripts/finalize-session.sh` | Writes `metrics.json`, `confidence.json`, `outcome.json`, SUMMARY; fail-open ledger + efficiency | Hook evaluation **after** line ~591 metrics/outcome write, same fail-open pattern as ledger (~593-612) |
| `scripts/lib/review_quality_ledger.py` | Per-session quality row + `_rollup/review-quality-ledger.jsonl` | Feed measurement from same finding buckets; do **not** duplicate as second ledger; extend or wrap |
| `scripts/aggregate-metrics.sh` | Cross-session metrics rollup | Keep; add parallel council-effectiveness aggregator |
| `scripts/lib/continuous_improvement.py` | Evidence Index pattern suggestions | Keep suggest-only; evaluation proposals are a **separate** ownership (`improvements/candidates/` under skill or local eval root) |
| `contracts/outcome-axes.schema.json`, finding/verification/runtime schemas | Authoritative shapes | Measurement references these; does not redefine |
| `config/observability-policy.yaml` | `auto_tune: false` | Add `evaluation:` block alongside |
| `docs/baselines/` | Empty | Bootstrap fixtures later; do not invent YONKO-DRY files that were never committed |
| No `evaluation/`, `evals/`, `MEASUREMENT-SCHEMA.md` | Gaps | Create under skill root |

## Architecture

```text
… existing review through three-axis outcome …
  → finalize core writes (metrics / confidence / outcome)   [unchanged authority]
  → evaluation capture (fail-open by default; policy may require)  [NEW]
       → evaluation/review-measurement.json
       → evaluation/council-effectiveness.{json,md}
       → evaluation/eval-candidate.json (mark only)
       → append measurement index event (rebuildable)
  → existing ledger + efficiency (unchanged)
```

Evaluation **never** mutates Packet, EG, findings, verifier, adjudication, or outcome.

## Data model (`contracts/evaluation/`)

| Schema | Purpose |
|--------|---------|
| `review-measurement.schema.json` | End-of-session measurement (null/unknown/not_run explicit) |
| `finding-adjudication.schema.json` | Per-finding taxonomy disposition |
| `seat-effectiveness.schema.json` | Per-seat measures (no sole score required) |
| `council-effectiveness.schema.json` | Session report envelope |
| `eval-case.schema.json` | Canonical / sanitised case |
| `eval-candidate.schema.json` | Auto-mark metadata |
| `eval-run.schema.json` | Replay result |
| `escaped-defect.schema.json` | Escaped defect + human-approved class |
| `ground-truth.schema.json` | Later attachment |
| `improvement-proposal.schema.json` | Suggest-only proposals with approval states |
| `failure-transcript.schema.json` | Sanitised runtime failures |
| `measurement-index-entry.schema.json` | Rebuildable index row |

### Finding outcome taxonomy (document in docs)

`accepted` | `accepted_as_sibling` | `merged` | `duplicate` | `downgraded` | `rejected_false` | `rejected_unsupported` | `rejected_out_of_scope` | `rejected_unreachable` | `rejected_pre_existing_not_worsened` | `verifier_inconclusive` | `chair_inconclusive` | `unknown_not_adjudicated`

Map from existing `findings.json` `accepted` / `dropped` / `held` + `dropped[].reason` / `action` where present; unknown when absent.

### Path quality (deterministic only on finalize)

Observable checks only (locus, reachability, impact, evidence refs present). Separate from outcome correctness. LLM grading: config off.

## Implementation modules

| Path | Role |
|------|------|
| `config/evaluation.yaml` | capture_on_finalize, reports, promote_automatically:false, llm_grading:false, ci_gate:false |
| `scripts/lib/evaluation/` | capture, taxonomy, effectiveness, aggregate, path_quality, sanitize |
| `scripts/capture-evaluation.sh` | CLI for one session (also called from finalize) |
| `scripts/aggregate-council-effectiveness.py` | Multi-session aggregate |
| `scripts/evals/promote-case.sh` | Two-gate promote (hash + approved-by + secret scan) |
| `scripts/evals/attach-ground-truth.py` | Ground truth attach |
| `scripts/evals/record-escaped-defect.py` | Escaped defect + human class approve |
| `scripts/evals/run-eval-suite.py` | Replay (frozen-packet \| full-pipeline) |
| `scripts/evals/compare-runs.py` | Baseline vs candidate |
| `scripts/evals/propose-improvement.py` | Proposal from aggregates (no apply) |
| `evals/` | cases/, manifests/, results/, escaped-defects/, README.md |
| `improvements/candidates/` | Proposal outputs (skill-local; never auto-write SKILL/prompts) |

## Finalize behaviour

1. After authoritative metrics/outcome written.
2. If `evaluation.capture_on_finalize: true` (default): build measurement + effectiveness + candidate mark.
3. Fail-open: write `evaluation/capture.error.txt`; do **not** fail finalize unless `evaluation.capture_required: true` (default false).
4. No paid model calls.
5. Preserve review outcome.

## Eval corpus / promotion

- Auto: `evaluation/eval-candidate.json` only.
- Promote: human `--approved-by` + `--confirm-hash` + secret scan; never silent overwrite.
- Packets: local reference by default; portable export sanitised.

## Replay

1. **Frozen-packet:** same Packet hash; vary models/prompts/profile for seats/verifier only via stubs in tests; live paid replay optional offline.
2. **Full-pipeline:** rebuild EG/Packet from fixture revision; labelled separately.
Never mutate production `execution-profile.json` from replay.

## Ground truth / escapes

Attach later outcomes without inventing correctness from “no incident”. Escaped defects become eval candidates after human classification approval.

## Continuous improvement relationship

- Existing `/yonko improve` = Evidence Index pattern frequency (unchanged).
- New proposals = evaluation-driven, separate CLI, human approval states (`proposed` → … → `rolled_back`), never auto-apply.

## Commands / UX

Script CLIs first; document slash equivalents for later:

- `/yonko metrics` → aggregate / show measurement
- `/yonko effectiveness` → council effectiveness
- `/yonko eval list|promote|run|compare`
- `/yonko ground-truth attach`
- `/yonko improvement propose`

Update `/yonko` command table in SKILL.md.

## Docs

Add: `docs/EVALUATION-SYSTEM.md`, `COUNCIL-EFFECTIVENESS.md`, `EVAL-CORPUS.md`, `GROUND-TRUTH-AND-ESCAPES.md`, `CONTINUOUS-IMPROVEMENT-EVAL.md` (or extend existing CI doc carefully).  
Update: README, DOCUMENTATION, ARCHITECTURE, SKILL, INVARIANTS (optional operational layer), CHANGELOG.

## Tests (deterministic, no paid calls)

Fixtures under `scripts/fixtures/evaluation/`. Smokes for capture, taxonomy, effectiveness, aggregate, promote gates, replay modes labelled, ground truth, failure transcript redaction, proposal no-apply.  
Regression: existing EG, cross-repo, packet/profile invariance, profiles, outcome axes, CI smoke, workflow, etc.

## Bootstrap

- Import safe metadata from existing sessions (e.g. BAF/ETS proof) as **candidates** with provenance `real_ticket` / verifier gaps honest.
- Do not fabricate verifier/ground truth.
- Empty `docs/baselines/` - create measurement schema docs; do not claim YONKO-DRY-001 exists if files absent.

## Versioning decision

**3.9.0** - evaluation is a new operational capability. Folding into 3.8.x would over-load the OpenCode/EG release line already at 3.8.2.

## Done when

Acceptance criteria from the human brief (1-20) are met; packet/profile invariance green; models/routing/EG/hash unchanged; no CI gate; no auto self-mod; no commit/push.

## Risks

| Risk | Mitigation |
|------|------------|
| Duplicate metrics vs ledger | Measurement wraps/extends ledger fields; single capture path |
| Finalize slow/fragile | Fail-open; no LLM; schema validate offline |
| Over-claiming recall | Document known-theme vs true recall |
| Small samples | Explicit sample-size warnings in aggregates |
| Scope explosion (full live replay) | Phase 1: capture + effectiveness + promote + stub replay; live OpenCode replay is opt-in offline |

## Phasing inside 3.9.0 MR-equivalent (single local land)

1. Schemas + config + capture on finalize + effectiveness report  
2. Taxonomy mapping + path-quality deterministic checks  
3. Aggregate + measurement index rebuild  
4. Eval candidate + promote  
5. Ground truth + escaped defect  
6. Replay + compare (fixtures/stubs)  
7. Improvement proposals  
8. Docs + smokes + regression  

Plan review should treat this as one plan with phased implementation, not five protocol changes.
