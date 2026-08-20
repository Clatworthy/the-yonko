# Yonko Evaluation System (3.9.0)

Observational measurement after authoritative finalize artefacts. Learning only - never auto-tunes seating, routing, prompts, models, Evidence Graph, Packet hash, verifier, adjudication, or three-axis outcomes.

## Ownership (Phase 1 decision)

```text
capture_session_observability()
  → evaluation/review-measurement.json   (canonical)
  → evaluation/council-effectiveness.*
  → evaluation/eval-candidate.json       (mark only)
  → _rollup/measurement-index.jsonl      (upsert by session_id)
  → ledger projection (legacy review-quality shape)
```

Capture does **not** import `review_quality_ledger`. The ledger may project from measurement when present; otherwise it uses the legacy builder.

## Finalize order

1. Write `metrics.json`, `confidence.json`, `outcome.json`
2. Shared capture
3. Write evaluation artefacts (atomic multi-file)
4. Upsert measurement index
5. Ledger projection upsert + rollup
6. `SUMMARY.md`
7. Efficiency report (fail-open)
8. `session.json` status=finalized

## Policy owners

| Concern | File |
|---------|------|
| `capture_on_finalize`, `fail_open` | `config/observability-policy.yaml` → `evaluation:` |
| paths, `min_sample_n`, retention, replay defaults | `config/evaluation.yaml` |

No auto-promote or CI-gate configuration keys. Promotion is only via `scripts/evals/promote-case.sh`.

## Commands

```bash
scripts/capture-evaluation.sh --session DIR
scripts/evals/aggregate-evaluation.py [--sessions-root DIR]
scripts/evals/rebuild-measurement-index.py
scripts/evals/promote-case.sh --session DIR --approved-by NAME --confirm-hash HASH
scripts/evals/replay-case.py --case-id ID --mode frozen_packet|full_pipeline
scripts/evals/compare-runs.py --run-a A --run-b B
scripts/evals/propose-improvement.py --proposal-id ID --title TEXT
scripts/evals/record-ground-truth.py --session DIR --outcome-type T --approved-by NAME
scripts/evals/record-escaped-defect.py --escaped-defect-id ID --source-session-id S ...
```

## Failure modes / recovery

| Trigger | Behaviour |
|---------|-----------|
| Capture exception (fail-open) | `evaluation/capture.error.txt`; finalize continues; metrics/outcome already written |
| `YONKO_EVAL_FORCE_CAPTURE_FAIL=1` | Test/debug only - forces capture failure through finalize |
| Re-finalize | upsert measurement index + ledger by `session_id` |

See `scripts/test-evaluation-system-smoke.py` for finalize artefact / upsert / fail-open coverage.
