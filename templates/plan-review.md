# Plan review templates (Yonko V3)

## Plan Docket (Chair writes; every seat receives)

Fill from the current chat, the ticket, and the reconnaissance the Chair actually did.
Do not invent acceptance criteria. Write `none stated` when absent.

```markdown
# Yonko Plan Docket

## Goal
<what the ticket asks for, in one or two sentences>

## Ticket / source material
- Ids: <TA-… / epic / none stated>
- Links: <URLs or none>
- Acceptance criteria as stated: <bullets, or none stated>
- Source files supplied to the packet: <names>

## Reconnaissance performed
- Repositories opened: <paths>
- Key symbols read: <path#symbol, one per line>
- Terminal persistence / external-effect leaves opened: <path#symbol, one per line>
- Contracts / events / schemas read: <list, or none>
- Existing documents read: <ADR/PAP/runbook, or none>

## Plan under review
- Artifact: <path to the drafted plan>
- Author: <Cursor draft in this chat | human | mixed>

## Done when (for the plan itself)
- [ ] every repository the change reaches is named
- [ ] contracts and version bumps are stated in order
- [ ] migration / deploy order / rollout / rollback are explicit or justified absent
- [ ] failure modes and concurrency are addressed
- [ ] testing strategy covers the risky branch
- [ ] material lifecycle and identity operations name exact keys, predicates, atomic boundaries, and retry outcomes
- [ ] mapper authority and canonical identity propagation are explicit
- [ ] tests assert terminal leaf effects, sibling cases, lost races, and retry exhaustion where applicable
- [ ] repair, soak, and operational completion criteria are measurable where applicable
- [ ] unresolved decisions are listed with owners
- Out of scope: <…>

## Golden path this plan sits beside
- Symbol/path: <existing production method or none stated>
- Steps: <3-8 short steps read from code, or none stated>
- Terminal leaf: <repository persistence method / queue publish / partner call, or none stated>
- Records / keys / external identities: <exact names and encoding, or none stated>
- Preconditions / ownership predicates: <per mutation, or none stated>
- Atomic boundary / partial-failure outcome: <what commits together and what survives failure>
- Plan should: match | intentional divergence: <what and why>

## Golden path excerpt
```
<≤ ~40 lines of the critical existing branch, or none stated>
```

## Explicit constraints
- No production code is written during this review
- No automatic continuation into implementation
- <project constraints, e.g. Java 21, model repo first, no docs in code MR>

## Known gaps / open questions
- <from chat, or none stated>

## Risk (from classify-scope-risk.sh)
- Band: <trivial|low|medium|high|critical>
- Basis: heuristic from stated scope and inspected context (NOT diff-derived)
- Reasons: <list>
- Terms not present in artifact (weak signal only): <list>
- Reviewers must hunt omitted scope themselves
```

## Packet

Build via script - never hand-assemble:

```bash
scripts/collect-plan-evidence.sh --session "$SESSION" --plan PLAN.draft.md \
  --source ticket.md --recon RECON.md --repo /abs/path/to/repo
scripts/classify-scope-risk.sh --session "$SESSION"
scripts/sanitise-and-hash-packet.sh --session "$SESSION" --docket PLAN_DOCKET.md
```

Packet shape:

```text
=== YONKO DOCKET ===
=== REVIEW TYPE ===
=== REPOSITORIES NAMED IN PLAN ===
=== TERMS NOT PRESENT IN ARTIFACT (weak signal only) ===
=== IMPLEMENTATION PLAN UNDER REVIEW ===
=== SOURCE MATERIAL: <name> ===
=== RECONNAISSANCE NOTES (paths and symbols already inspected) ===
```

Same packet for every seat. Never include other seats' findings mid-round.

---

## Round Bulletin (plan review)

```text
Yonko Plan Round <n> - <plan title>
Risk: <band> (heuristic from stated scope)
Seated: <seats> | Packet: v<version> <hash12>

Findings: <n> (critical <c> / high <h> / medium <m> / low <l>)
Accepted into the revised plan: <n>
Held for human decision: <n>
Rejected as ungrounded: <n>

Top gaps:
- <severity> <title> - <one line consequence>

Chair revision: PLAN.revised.md written
Next: <confirmation round | human approval>
```

---

## PLAN.approved.md (handoff artifact)

The Chair writes `PLAN.revised.md` after adjudication. It becomes `PLAN.approved.md`
**only** when the human approves. The Chair never approves its own plan.

```markdown
# Approved implementation plan

- Plan session: <session id>
- Approved by: <human name or handle>
- Approved at: <UTC timestamp>
- Risk band at review: <band> (heuristic from stated scope and inspected context)
- Engineering Confidence in this plan: <HIGH|MEDIUM|LOW>

## Scope
- Repositories: <every repo the change touches>
- Services / consumers affected: <list>
- Contracts changed: <API / event / schema, with version bumps>
- Explicitly out of scope: <list>

## Implementation steps (ordered)
1. <step, with the repository it lands in>
2. …

## Contracts and version order
- <model repo MR first, published jar tag, then consuming service lockfile bump, etc.>

## Migration and data
- <migration, backfill, two-phase deploy, or "none required because …">

## Deployment order
- <what deploys first, what breaks if reversed>

## Rollout and rollback
- Rollout: <flag, elevate-to-Beta, staged>
- Rollback: <abort path once half-deployed>

## Failure modes and concurrency
- <retries, idempotency, partial failure, race conditions and how the plan handles them>
- <bounded retry count/backoff, optimistic or conditional conflict handling, and exhaustion outcome>

## Lifecycle and identity contracts
- Terminal leaves: <persistence methods / publish methods / partner calls inspected>
- Records and keys: <every record and exact key encoding changed>
- Ownership predicates: <condition for each write, delete, archive, or cleanup>
- Atomic boundaries: <operations that commit together; compensation or surviving state on failure>
- Mapper authority: <authoritative identity and whether weaker fallback is forbidden>
- Canonical identity propagation: <old value to new value across storage, mirrors, indexes, events, and responses>
- Conflict policy: <one rule used by online adoption and repair>

## Compatibility
- <existing callers, stored data, events, clients>

## Testing strategy
- <unit / integration / the specific risky branch each test pins>
- <terminal leaf effect, sibling/shared-owner, lost race, retry-exhausted, and old-to-new identity tests where applicable>

## Operational acceptance
- <repair completion query, soak duration/signal, retry-exhausted metric/alarm, and decision owner>
- <state that proves rollout complete; resource existence alone is not completion proof>

## Decisions made during review
- <decision> - <rationale>

## Assumptions
- <assumption> - <how it was verified, or that it is unverified>

## Open questions for the human
- <question> - <owner>

## Findings rejected during review
- <finding title> - <why rejected: ungrounded / not applicable / duplicate>

## Remaining risks
- <risk> - <mitigation or accepted>

## Handoff
- Implementation review must reference this file and this plan session id in its Docket.
- Deviations from this plan must be listed in the implementation Docket with reasons.
```

---

## Human runway (plan review)

```text
Plan review complete. Nothing has been implemented and nothing was committed.

1. Read: <session>/PLAN.revised.md
2. Held for your decision: <n items>
3. Open questions: <n>
4. Engineering Confidence: <LEVEL> because <reasons>

To approve: copy PLAN.revised.md to PLAN.approved.md (or tell me to), then implement.
To reject: tell me what to change and I will run one more revision.

I will not start implementing until you approve.
```
