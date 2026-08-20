# Eval corpus

Human-gated corpus under `evals/`.

## Lifecycle

1. Finalize writes `evaluation/eval-candidate.json` (`promoted: false`)
2. Human promotes with:

```bash
scripts/evals/promote-case.sh \
  --session DIR \
  --approved-by NAME \
  --confirm-hash <packet.meta.json packet_hash> \
  [--case-id ID] [--overwrite]
```

3. Case lands in `evals/cases/<case_id>.json`
4. Replay writes under `evals/results/<run_id>/` only

## Promote refuse matrix

| Condition | Exit |
|-----------|------|
| Missing required args | 2 |
| Empty / mismatch hash | 3 |
| Secret scan fail | 4 |
| Case exists without `--overwrite` | 5 |
| No eval-candidate | 6 |

## Replay modes

| Mode | Hash rule |
|------|-----------|
| `frozen_packet` | must match case `packet_hash` |
| `full_pipeline` | new hash allowed; never compare as frozen |

`compare-runs.py` rejects cross-mode compares.
