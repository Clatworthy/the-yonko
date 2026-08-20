# Optimisation quality acceptance (dual packet)

Use after any packet representation change (dedupe, handoff slim, future compression).

## Principle

Yonko may optimise representation, but it must never optimise away engineering information.

## Steps

1. Produce **Original packet** (optimisation off / pre-transform).
2. Produce **Optimised packet** (optimisation on).
3. Mechanical check:

```bash
scripts/compare-optimisation-quality.sh \
  --original /path/to/original-session-or-packet.md \
  --optimised /path/to/optimised-session-or-packet.md \
  --out /tmp/yonko-opt-compare
```

Must pass: DIFF bodies identical, fenced code identical, dedup refs resolvable.

4. Seat the same council on both packets (identical seats, risk band, prompts aside from packet body).
5. Fill `council-compare.json`:

```json
{
  "fixture_id": "example-impl-1",
  "original_session": "~/.cursor/yonko-sessions/…",
  "optimised_session": "~/.cursor/yonko-sessions/…",
  "material_findings_original": 4,
  "material_findings_optimised": 4,
  "finding_categories_original": ["concurrency", "security", "testing"],
  "finding_categories_optimised": ["concurrency", "security", "testing"],
  "severity_downgrades": [],
  "evidence_unresolvable": [],
  "verifier_weakened": false,
  "confidence_original": "high",
  "confidence_optimised": "high",
  "justified_differences": [],
  "notes": ""
}
```

Coverage categories (map each material finding; same count with different classes is a fail):
`correctness`, `architecture`, `concurrency`, `security`, `performance`,
`compatibility`, `testing`, `operability`, `data-integrity`, `api-contract`, `other`.

Example fail: original `[concurrency, security]` vs optimised `[concurrency, documentation]` -
still two findings, but security coverage was lost.

6. Re-run with `--council-json council-compare.json`. Exit 0 only if unexplained differences are empty.

## Pass rule

The optimised packet must not produce fewer material findings, **lose finding coverage
categories**, or weaker evidence unless each difference is explicitly justified in
`justified_differences`.

Token savings: do not target a percentage. Measure after 10–20 real Efficiency Reports.
