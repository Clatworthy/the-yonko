# Council effectiveness

Per-session report composed from `evaluation/review-measurement.json`.

Artefacts:

- `evaluation/council-effectiveness.json`
- `evaluation/council-effectiveness.md`

## Contents

- Per-seat status (`completed` / `not_run` / `failed` / `unknown`)
- Raw / unique finding counts when known
- Duration and cost from `runtime/<seat>/result.json` when present
- Disposition counts from the honest taxonomy overlay
- Path-quality status (review-type-specific)
- `insufficient_sample` relative to `config/evaluation.yaml` `min_sample_n` (default 10)

## Rules

- Session-level sample size is 1; strong protocol claims require aggregate `n >= min_sample_n`
- Aggregate: `scripts/evals/aggregate-evaluation.py` writes `_rollup/evaluation-aggregate.json` with `insufficient_sample` and `strong_claims_allowed`
- Never the sole basis for changing models, seats, routing, or prompts
