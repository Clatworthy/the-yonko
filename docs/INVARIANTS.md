# Yonko Architectural Invariants

These are expected to remain stable. Any proposal that changes one must
demonstrate a measurable improvement that outweighs the cost.

They are the criteria against which future protocol proposals are judged.
They are not a feature list.

## Invariants

1. **One shared evidence packet.** Every seated reviewer starts from the same hashed evidence. An execution profile may permit logged read-only repository discovery, but it cannot replace or mutate the packet.
2. **Independent reviewers.** Seats judge the full packet without seeing each other’s findings first.
3. **Deterministic routing.** Policy and scripts select seats; models do not invent the roster.
4. **Verification is separate from judgement.** Reviewers discover; the verifier confirms; votes never prove.
5. **Workflow owns legality.** Scripts answer whether a session is procedurally complete.
6. **Humans own authority.** Commit, push, publish, and protocol edits stay with humans.
7. **No optimisation without evidence.** Metrics inform humans; they never silently retune seating, packets, retrieval, or apply rules.
8. **Proportionality.** Risk band sizes protocol cost; the safety floor blocks unsafe downgrades.

## Related ownership (stable)

| Concern | Owner |
|---------|--------|
| Evidence | Packet |
| Change-impact / cross-repo completeness | Evidence Graph + Evidence Index (`docs/EVIDENCE-GRAPH.md`) |
| Which runtime/model invokes a seat | Execution profile (`docs/EVIDENCE-EXECUTION-SEPARATION.md`) |
| Seating / floors | Routing policy (`band_floor`, `band_baseline`) |
| When verification is required | Verification policy (`require_verifier_bands`) |
| Procedural completeness | Workflow |
| Engineering judgement | Council seats |
| Confirmation of material claims | Verifier |
| Ship authority | Human |

**Separation:** Evidence Graph decides the authoritative starting scope. Execution
profile decides *which runtime* reviews the already-hashed packet and whether that
runtime may perform logged read-only discovery. Profiles must not rebuild graph
evidence, re-derive completeness, or change three-axis outcome semantics.
Discovery outside the packet produces suggest-only graph-gap candidates.

## Optional operational layers (not invariants)

These may grow, shrink, or stay unused without changing the core protocol:

- Evidence Index
- Continuous Improvement (suggest-only)
- Efficiency / observability reporting
- Evaluation / council effectiveness (3.9.0; observational; ledger projection; human-gated corpus)
- Execution profiles (runtime/model selection per seat; recommended `cursor-opencode-go`; missing-marker fallback `cursor-standard`)

## Protocol feature gate

No new **protocol** feature unless:

1. At least three independent review sessions demonstrate the same unmet need, **and**
2. The proposed change preserves all architectural invariants above.

Documentation and configuration hygiene (dead mirrors, ownership consolidation, clearer newcomer docs) is not a protocol feature and may proceed under freeze.

## Newcomer mental model (five concepts)

Enough to be productive on an implementation review:

1. **Chair** - parent agent; sole writer
2. **Packet** - shared hashed evidence (the Docket is the human brief inside it)
3. **Council** - independent reviewers
4. **Risk band** - how heavy the review is (inferred from seating)
5. **Human authority** - you approve, commit, and publish

Everything else (routing policy detail, workflow JSON, Evidence Index, continuous improvement) can wait.
