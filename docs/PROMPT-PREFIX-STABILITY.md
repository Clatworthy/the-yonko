# Prompt prefix stability

Yonko builds reviewer prompts so provider prompt-cache can reuse a long shared
prefix. Cache hits are **best-effort observability only**. Correctness must not
depend on a cache hit.

## Ordering (promptFormatVersion 1)

```text
1. Stable protocol     (no seat / path / session / model / timestamp)
2. Evidence Packet     (inline text + packet_hash label)
3. Finding schema      (deterministic JSON: sort_keys, compact)
4. Seat suffix         (identity + attention bias + Attack card rows)
5. Volatile run suffix (attempt number, optional repair errors)
```

Shared prefix = sections 1-3. Variable suffix = sections 4-5.

## Fingerprints

| Field | Meaning |
|-------|---------|
| `sharedPrefixHash` | SHA-256 of sections 1-3 only |
| `fullPromptHash` | SHA-256 of the entire prompt |
| `sharedPrefixBytes` / `fullPromptBytes` | UTF-8 sizes |

Same Packet + same schema → same `sharedPrefixHash` across seats.
Repair re-invokes with a new volatile suffix only; shared prefix hash must match
attempt 1 or the adapter refuses the repair.

## Where it lives

| Artefact | Role |
|----------|------|
| `scripts/lib/runtime/prompt_builder.py` | Deterministic builder |
| `runtime/<seat>/prompt.txt` | Full prompt written for the seat |
| `runtime/<seat>/prompt.meta.json` | Hashes / bytes |
| `runtime/<seat>/result.json` → `prompt` | Observability (incl. cache metrics when provider reports them) |

## Cache metrics

`cacheHit` is true only when the provider reports `cache_read > 0`.
Never infer hit from latency. Different models cannot share one cache.

## What this does not change

- Evidence Graph, Packet bytes, or Packet hash
- Model selections / execution profiles / seat routing
- Validation / adjudication / outcomes
- No custom KV cache; no shared sessions across seats

## Related

- [`EXECUTION-PROFILES.md`](EXECUTION-PROFILES.md) - runtime adapters
- [`EVIDENCE-EXECUTION-SEPARATION.md`](EVIDENCE-EXECUTION-SEPARATION.md) - Packet vs runtime
- Smoke: `scripts/test-prompt-prefix-stability-smoke.py`
