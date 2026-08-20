# Ground truth and escaped defects

Human-approved later truth. Observational only.

## Ground truth

```bash
scripts/evals/record-ground-truth.py \
  --session DIR \
  --outcome-type <type> \
  --approved-by NAME \
  [--finding-id ID ...]
```

Writes `evaluation/ground-truth.json` (or `evals/cases/<id>.ground-truth.json`).

## Escaped defects

```bash
scripts/evals/record-escaped-defect.py \
  --escaped-defect-id ID \
  --source-session-id SESSION \
  --failure-classification missed_finding|false_accept|false_reject|path_quality_gap|verifier_gap|other \
  --human-approved-by NAME
```

Writes `evals/escaped-defects/<id>.json`.

Never invent dry-run placeholder ids. Bootstrap from real sessions with honest provenance only.
