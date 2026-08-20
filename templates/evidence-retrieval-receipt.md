# Historical evidence retrieval receipt

Advisory only. Does not change risk bands, seating, adjudication, or apply rules.
Superseded and contradictory records stay visible with lifecycle / status labels.

Prefer generating via:

```bash
scripts/evidence-index.py query --from-repo \
  --similar \
  --api /v1/example \
  --like-concept auth \
  --like-repository services/example \
  --artifact-type pap
```

Chair pastes selected rows into the neutral packet under **Historical evidence**.
Record consumed ids as `informed_by` when building the next candidate.

```markdown
### Historical evidence receipt

Scoring version: <scoring_version>
Query terms: <explicit tickets / repos / services / contracts / human-confirmed concepts>

| evidence_id | score | matched fields | lifecycle | final_status | confidence | artifact path |
|---|---|---|---|---|---|---|
| ... | ... | api=/v1/...(+5), concept=auth(+2) | canonical | approved | high | records/YYYY/.../artifacts/... |

Selected for packet: <evidence_ids>
Rejected / not used: <evidence_ids + reason>
```

Rules:

- Build query terms from explicit identifiers and human-confirmed concepts only.
- Never silently treat superseded or contradictory records as current truth.
- Historical evidence cannot auto-seat reviewers or change risk.
