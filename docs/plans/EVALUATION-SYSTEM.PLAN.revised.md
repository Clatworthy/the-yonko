# PLAN: Yonko Evaluation and Council Effectiveness (3.9.0)

**Status:** revised after plan review (session `yonko-eval-system-plan`, packet `28e5fc37…`)  
**Skill root:** `~/.cursor/skills/the-yonko/`  
**Current VERSION:** 3.8.2 → **target 3.9.0**  
**Prior draft:** `docs/plans/EVALUATION-SYSTEM.PLAN.draft.md`

## Goal

Observational evaluation layer: measurement at finalize, council effectiveness, human-gated eval corpus, frozen-packet / full-pipeline replay, ground truth / escaped defects, improvement proposals **without** auto-apply.

## Non-goals (hard)

Unchanged: no Packet/EG/cross-repo/risk/routing/seat-count/finding/verifier/adjudicator/outcome-axis semantic changes; no model-selections or execution-profile default changes; no auto-tune, auto-commit/push, CI gate, prompt rewrite; no LLM grading on finalize; no inventing `YONKO-DRY-*` / `MEASUREMENT-SCHEMA.md` files that do not exist.

**Hard-code (not config flags):** promote is **only** via `promote-case.sh` gates. Do **not** ship `promote_automatically` or `ci_gate` keys (even as `false`) - they are footguns.

---

## Grounded inventory (reuse)

| Existing | Role |
|----------|------|
| `finalize-session.sh` | Authoritative metrics/confidence/outcome; ledger ~593-612; efficiency ~677-692; then session finalized |
| `review_quality_ledger.py` | Finding buckets + runtime/ walk + verifier/cost fields |
| `aggregate-metrics.sh` | Descriptive rollup only |
| `continuous_improvement.py` | Evidence Index patterns - **separate** from evaluation proposals |
| `evidence-index.py` `load_verifications` / `split_findings` | Import/reuse for verifier mapping |
| Real fixtures | `scripts/fixtures/evaluation/sample-adjudication-findings.json` (accepted/dropped with `action`/`reason`/`sources`); plan sessions with `plan_findings` only |

---

## Finalize order (authoritative - resolves diagram conflict)

Exact sequence inside finalize Python after workflow gate:

```text
1. Write metrics.json, confidence.json, outcome.json     [authoritative]
2. Shared capture: lib/evaluation/capture.build_measurement(session_dir)
   - reads findings, runtime/, verification, session, packet.meta, outcome, routing
   - may call review_quality_ledger.build_row() OR read review-quality.json if already written
3. Write evaluation/review-measurement.json (+ path_quality overlay)
4. Write evaluation/council-effectiveness.{json,md}
5. Write evaluation/eval-candidate.json (mark only; never promote)
6. Upsert measurement index (~/.cursor/yonko-sessions/_rollup/measurement-index.jsonl)
   keyed by session_id (idempotent re-finalize)
7. Existing ledger upsert (or: ledger becomes thin wrapper calling shared capture - Phase 1 picks ONE)
8. SUMMARY.md
9. efficiency report (fail-open)
10. session.json status=finalized
11. shell: record-event session_finalized
```

**Preferred Phase 1 ownership:** one function `capture_session_observability(session_dir)` returns a structured row; ledger row is a **projection** of that row (or embeds `measurement_id` / field references). `review-measurement.json` is evaluation SoT for new fields; ledger keeps backward-compatible rollup shape.

**Fail-open:** default. On capture error → `evaluation/capture.error.txt`; finalize continues.  
**Opt-in fail-closed:** `observability-policy.yaml` → `evaluation.fail_open: false` (mirror efficiency). When fail-closed, after authoritative writes, exit non-zero (document code) and use atomic tempdir+rename for multi-file evaluation outputs.  
**Do not** put fail_open only in a second file without precedence: **observability-policy owns fail_open / capture_on_finalize**; `config/evaluation.yaml` owns corpus paths, min_sample_n, retention, replay defaults only.

---

## Data model (`contracts/evaluation/`)

Schemas each include `"schema_version": 1` (const). Additive-only later.

| Schema | Purpose |
|--------|---------|
| review-measurement | Session measurement |
| finding-adjudication | Per-finding disposition |
| seat-effectiveness | Per-seat measures |
| council-effectiveness | Session report |
| eval-case / eval-candidate / eval-run | Corpus + replay |
| escaped-defect / ground-truth | Later truth |
| improvement-proposal | Suggest-only |
| failure-transcript | Sanitised failures |
| measurement-index-entry | Rollup index row |

### validate-artifact kinds (Phase 1 minimum)

Extend `validate-artifact.sh` with: `review-measurement` | `council-effectiveness` | `eval-case` | `eval-run`.

### Contracts / version order

1. evaluation schemas + validate-artifact kinds  
2. observability-policy `evaluation:` + evaluation.yaml (paths only)  
3. shared capture + finalize hook  
4. aggregate / index rebuild  
5. promote / ground-truth / escape / replay / compare / propose  
6. docs + smokes  

Bump skill `VERSION` to **3.9.0** when landing.

---

## Finding outcome taxonomy (normative mapping)

**Adjudication state** (required on measurement):  
`complete` | `partial` | `pre_adjudication` | `plan_array_form` | `document_array_form` | `empty_findings`

### Inferrable today (deterministic)

| Source | Disposition |
|--------|-------------|
| In `accepted[]` | `accepted` (default) |
| Same fingerprint as another accepted; second seat | `duplicate` or `accepted_as_sibling` only when Chair notes / same-id cluster rules say so - else `duplicate` if title/path match ledger fingerprint |
| `dropped[]` with `action` in `drop`,`dropped` + reason empty | `unknown_not_adjudicated` (**never** invent `rejected_*`) |
| `dropped[]` reason contains unsupported / no evidence (casefold heuristics documented) | `rejected_unsupported` |
| `dropped[]` reason out of scope / pre-existing | map only if substring tables documented; else unknown |
| `held[]` | `chair_inconclusive` or `unknown_not_adjudicated` |
| Verifier rejected id in verification artefact | contribute `verifier` provenance; Chair drop still required for final disposition |
| No adjudication buckets; only `findings` / `plan_findings` / seat runtime findings | `pre_adjudication` / `plan_array_form` - dispositions `unknown_not_adjudicated` |
| `notes[]` | excluded from finding dispositions (separate notes count) |
| `sources[]` without `reviewer` | map seat from sources like ledger |

**Not inventable without new Chair fields:** `rejected_false`, `rejected_unreachable`, `rejected_pre_existing_not_worsened`, `merged`, `downgraded` (except when `original_severity`/`severity_before` present → `downgraded`), `accepted_as_sibling` without cluster metadata.

Future: optional Chair adjudication schema extension - **separate** additive change; until then unknown is honest.

Worked examples in docs from sample adjudication + smoke-plan fixtures.

---

## Path quality (by review_type)

| Type | Checks (n/a ≠ fail) |
|------|---------------------|
| implementation | locus, evidence, reachability, impact present |
| plan | evidence_kind, evidence_reference, production_consequence; locus optional |
| document | evidence_kind, evidence_reference, impact (per document-finding schema) |

Empty findings with seats completed → `path_quality.status=not_applicable`, `measurement.flags` includes `empty_findings`; **do not** vacuous-pass; **do not** strong-mark eval candidate.

---

## Runtime inputs

Measurement **must** walk `runtime/<seat>/` like ledger (`findings.json`, `result.json`, optional `dispatch.json`). Missing runtime/ → per-seat `not_run` / unknown - never silent zero.

---

## Council effectiveness

Compose from measurement + taxonomy overlay. Raw metrics first. Optional composite score only if formula documented and never sole basis. Sample-size: `insufficient_sample` when n < `evaluation.min_sample_n` (default 10); proposals **refuse** strong protocol claims below N.

---

## Eval corpus / promote

- Auto: `evaluation/eval-candidate.json` only. Weak/empty findings → candidate reason `weak_or_empty` or omit strong reasons.
- Promote gates (**all required**): `--approved-by`, `--confirm-hash` = `packet.meta.json` `packet_hash` (document algorithm), secret scan pass, case-id unique unless `--overwrite` + approve.
- Refuse matrix with exit codes; smoke missing/mismatch/empty hash.
- No `promote_automatically` config key.

---

## Replay

| Mode | Label | Packet |
|------|-------|--------|
| frozen-packet | `replay_mode=frozen_packet` | same hash required |
| full-pipeline | `replay_mode=full_pipeline` | new hash allowed; never compare as frozen |

Isolation: profile/model overrides only under `evals/results/<run>/`; **forbid** calling `set-execution-profile.sh`; regression asserts skill `config/execution-profile.json` fingerprint unchanged.

`compare-runs.py` rejects cross-mode compare.

---

## Measurement index

Path: `~/.cursor/yonko-sessions/_rollup/measurement-index.jsonl`  
Upsert by `session_id`. Rebuildable by scanning `**/evaluation/review-measurement.json`. Optional `record-event` type `evaluation_captured` (observational).

---

## Improvement proposals

Separate from Evidence Index `/yonko improve`. Output under `improvements/candidates/`. States: proposed → approved_for_experiment → … → rolled_back. Never write SKILL/prompts/routing. Require evidence links + sample size; refuse strong claims when `insufficient_sample`.

---

## Failure modes / recovery

| Trigger | Behaviour |
|---------|-----------|
| Capture exception (fail-open) | capture.error.txt; continue finalize |
| Partial multi-file write | tempdir + rename; else leave error + no half index row |
| Re-finalize | upsert measurement + index by session_id |
| Ledger ok / eval fail | index may note gap; ledger row still valid |
| Missing findings.json | measurement with empty_findings / unknown |

Commands: `capture-evaluation.sh --session`, `rebuild-measurement-index.py`.

---

## Bootstrap

Import **candidates** from real sessions (e.g. BAF/ETS proof) with honest provenance (`verifier_absent` / `real_ticket`). Never create YONKO-DRY placeholder ids. `docs/baselines/` may gain schema docs only - not fake dry baselines.

---

## Phasing (single 3.9.0 land, ordered)

1. Schemas + validate-artifact kinds + shared capture + finalize hook + effectiveness  
2. Taxonomy matrix + path_quality by review_type + empty/partial/runtime-missing smokes  
3. Aggregate + index rebuild + insufficient_sample  
4. Promote refuse matrix  
5. Ground truth + escaped defect  
6. Replay + compare + profile fingerprint invariance  
7. Improvement propose  
8. Docs + full regression  

---

## Tests (must include adversary cases)

- Taxonomy: adjudication-bucket-shaped dropped; plan_findings-only; empty findings; held  
- Finalize order smoke; capture fail-open; fail-closed opt-in  
- Missing runtime/  
- Promote: missing/mismatch hash refuse; no overwrite without flag  
- Replay: frozen hash stable; profile fingerprint unchanged; cross-mode compare reject  
- insufficient_sample on aggregate/propose  
- No YONKO-DRY string in bootstrap outputs  
- Packet/profile invariance + existing suites green  

---

## Docs

`docs/EVALUATION-SYSTEM.md`, `COUNCIL-EFFECTIVENESS.md`, `EVAL-CORPUS.md`, `GROUND-TRUTH-AND-ESCAPES.md`, plus CI staged rollout (observe → offline replay → advisory → optional gate later). Update SKILL/README/DOCUMENTATION/ARCHITECTURE/CHANGELOG/INVARIANTS (optional layer).

---

## Done when

Human brief acceptance 1-20, plus revised plan constraints above. No commit/push. Models/routing/EG/hash unchanged.

## Human runway

Approve this revised plan (`PLAN.approved.md`) before implementation. Plan review session: `~/.cursor/yonko-sessions/yonko-eval-system-plan`.
