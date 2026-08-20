# End-of-run summary + Engineering Confidence

Observational only. Does not change seating, apply rules, or adjudication.
Applies to all three review types (implementation, plan, document).

Prefer generating via script (also writes `outcome.json` and, after that, `evaluation/*` + ledger projection):

```bash
scripts/finalize-session.sh --session "$SESSION" \
  --verdict pass \
  --confidence high \
  --reason "packet complete" \
  --reason "risk reviewed" \
  --reason "verification complete" \
  --reason "deterministic checks passed" \
  --reason "deployment straightforward"
```

## Engineering Confidence (print first on final Verdict)

```markdown
### Engineering Confidence

**HIGH** | **MEDIUM** | **LOW**

because:
- ✓|✗|? packet complete
- ✓|✗|? evidence collected for <review type>
- ✓|✗|? risk reviewed (<diff-derived | heuristic from stated scope and inspected context>)
- ✓|✗|? verification complete (or n/a for route)
- ✓|-|? deterministic checks passed / recorded    (- = n/a for plan and document)
- ✓|✗|- handoff artifact written                  (- = n/a for implementation)
- ✓|✗|? deployment straightforward
- ✓|✗|? Done when honest / no open product ambiguity
- ✓|✗|? assumptions remaining stated
- ✓|✗|? human decisions still required are listed

Chair one-liner: <why this band>
```

Guidance (prompt-orchestrated honesty, not auto-tuned):

| Band | When |
|------|------|
| HIGH | Packet hashed, evidence complete, risk classified, verify done or n/a, scoped checks green or n/a, handoff written, deploy path clear, Done when met |
| MEDIUM | Pass/Remand with notes, skips, residual holds that are not product-blocking, or a missing handoff artifact on a pass |
| LOW | Deadlock, thrash, verify-red, incomplete packet or evidence, open product ambiguity, or a document finalized in create mode with no draft |

Never inflate to HIGH to please. Confidence is for the human, not the models.

**Never treat HIGH + legacy Verdict Pass as "safe across the whole system" when
evidence completeness is incomplete.** Cap confidence at MEDIUM in that case
(`finalize-session.sh` does this mechanically when `outcome.json` says so, including
clamping Chair `--confidence` to the ceiling). When unresolved categories include
`operational_side_effects` or `cross_repository_consumers`, `clean_pass_allowed`
is false - headline **Pass with unresolved evidence (...categories...)**, never sole
Pass / push-ready / clean.

## Outcome axes (print with every final verdict)

```markdown
### Outcome axes

- Headline: Pass | Pass with unresolved evidence (...) | Findings remain | ...
- Clean pass allowed: true | false
- Review outcome: pass | findings | inconclusive
  - pass = No validated defects found (not system-wide safety)
- Evidence completeness: complete | incomplete
  - e.g. Incomplete - external consumers unresolved
- Deployment recommendation: proceed | proceed_with_caveat | block
```

Written to `outcome.json`, `metrics.json`, and `SUMMARY.md` by `finalize-session.sh`.

```markdown
# Yonko session summary

- Session: <id>
- Review type: implementation | plan | document (<artifact>/<mode>)
- Legacy protocol verdict: …
- Review outcome: pass|findings|inconclusive - …
- Evidence completeness: complete|incomplete - …
- Deployment recommendation: proceed|proceed_with_caveat|block - …
- Mode / risk: … (<basis>)
- Duration: …
- Rounds: …
- Task calls: N / budget M
- Packet: vN / <hash12> / <bytes> bytes
- Unique findings by seat: Shanks N, Blackbeard N, Buggy N, Luffy N
- Verifier: confirmed A / rejected B / inconclusive C (reject rate …%)
- Handoff artifact: <path or MISSING>       (plan / document only)
- Linked session: <plan session id or n/a>
- Applies: …                                (implementation)
- Artifact revisions: …                     (plan / document)
- Deploy notes: …

## Engineering Confidence
…

## Human runway
…
```

## Passive metrics

`metrics.json` is for learning across sessions. **Never** feed into routing or apply decisions automatically.

Collected per session: findings by reviewer, unique findings by reviewer,
rejected/ungrounded rate, reviewer completion, route and Task count, confirmation/rematch
count, duration, packet bytes, review type, artifact type.

Cross-session rollup:

```bash
scripts/aggregate-metrics.sh
scripts/aggregate-metrics.sh --type plan          # or implementation | document
# writes ~/.cursor/yonko-sessions/_rollup/metrics-rollup.json
```

## Evidence Index (optional, post-finalize)

`finalize-session.sh` reports candidate eligibility only. To stage and publish locally:

```bash
scripts/evidence-index.py candidate --session "$SESSION" \
  --owner <you> --final-status <status> --ticket <TA-...>
scripts/evidence-index.py publish-local --session "$SESSION" \
  --candidate-hash <sha256> --approved-by <you>
```

Never auto-run publish from finalize. Never git commit/push from Yonko.

## Engineering Efficiency Report (V4 direction - observational)

When implemented, emit after Engineering Confidence. Do **not** auto-tune from it.

```markdown
### Engineering Efficiency Report

#### Packet
- Estimated tokens: …
- Compression: … (or none)
- Largest sections: …
- Repeated material: …

#### Review
- Seats invoked: …
- Reviewer / Chair loops: …
- Verification runs: …
- Material findings / rejected: …
- Final confidence: …

#### Knowledge
- Historical matches used: …
- Evidence added: …
- Relationships added: …
- Concepts indexed: …

Observational only. Metrics inform humans; humans change Yonko.
```

