# Workflow legality (V3.4) - session artefact only (not a runtime)

## First principle

```text
Protocol governs process.
Evidence governs decisions.
```

Yonko may optimise representation, but it must never optimise away
engineering information.

**Workflow legality** landed as a V3.4 milestone and still applies at current `VERSION`
(3.8.x harness: Evidence Graph, execution profiles, three-axis outcomes). Use it on
real work. Collect friction. Let observed failure cases drive the next change - not
ambition alone.

Files (inside each session directory):
- `workflow.json`
- `workflow-events.jsonl`
- `workflow.lock` (fcntl; best-effort)
- `human-approval.json` (explicit human approval metadata)
- `human-override.json` (narrow legality override)

No daemon, database, Docker, or third-party dependency.
Every operation is a short-lived script that updates files and exits.

## Modes

| Mode | Default for new sessions | On guard violation |
|------|--------------------------|--------------------|
| `enforce` | yes (`config/workflow-policy.yaml`) | record `blocked`, non-zero exit, do not claim success |
| `shadow` | override via `YONKO_WORKFLOW_MODE=shadow` | record `would_block`, exit 0 (V3.3 behaviour) |

Legacy sessions without workflow artefacts remain usable. First observation
initialises state safely. Unknown history is not fabricated as compliant.

V3.3 sessions that already have `mode: shadow` in `workflow.json` stay shadow
unless `YONKO_WORKFLOW_MODE` overrides.

## Principle split

| Layer | Owns |
|-------|------|
| Workflow legality (this layer) | Process: ordering, pin freshness, seat counts, verify presence, open material severity+disposition, human approval metadata, budgets, write fences |
| AI / Chair | Judgement: finding content, materiality beyond severity/disposition, adjudication, remediation quality |
| Human | Final authority: plan/document approval, Evidence Index publish, legality overrides |

## States and invariants

| State | Guaranteed facts |
|-------|------------------|
| INIT | session.json exists with review_type |
| EVIDENCE_READY | type-correct evidence artefacts under evidence/ |
| RISK_SET | evidence/risk.json or evidence/scope-risk.json exists |
| PACKET_PINNED | packet.md exists; session.packet_hash matches file SHA-256; fingerprints stored |
| SEATED | reviewers_seated observed; seat_count meets band minimum |
| FINDINGS_VALID | validate-artifact succeeded for a findings kind |
| VERIFIED | verification_completed / scoped_verify success when band requires verify |
| APPLIED_OR_REVISED | apply or artifact_revised observed |
| SCOPED_OK | scoped_verify observed (implementation) |
| AWAITING_HUMAN | plan/document runway (approval metadata path) |
| FINALIZED | finalize completed under active mode rules |
| PUBLISHABLE | evidence publish path observed |

`ADJUDICATED` is not a legality state - disposition remains Chair-owned.

## Transitions

Option A: single `pin_packet`. Auto `invalidate_packet` when Docket/evidence/linked-plan
fingerprints diverge after pin. Legitimate repin restores legality.

Critical transitions (enforce, no state advance on block):
finalize, seat_reviewers, pin_packet, apply_or_revise, human_approve_artifact,
rematch, publish_evidence.

## Authoritative guards (enforce)

| Code | Rule |
|------|------|
| OPEN_MATERIAL_FINDINGS | pass with unresolved medium/high/critical findings (disposition contract) |
| VERIFICATION_REQUIRED | band requires verify and success evidence absent/unsuccessful |
| REVIEWER_INCOMPLETE | seat_count below configured minimum for risk band, or seated seats[] missing a routing.json required seat |
| OPENCODE_EXECUTE_MISSING | routed OpenCode seat still awaiting_chair_dispatch / missing findings.json (Task never ran --execute) |
| ORG_SHIP_GATE_REQUIRED | Implementation pass without post-council `run-org-ship-gate.sh` result when adapter enables org_ship_gate |
| ORG_SHIP_GATE_FAILED | Org ship gate Remand / non-empty findings / incomplete Attack card |
| HUMAN_APPROVAL_REQUIRED | plan/document pass without explicit human-approval.json |
| PACKET_STALE | Docket/evidence/linked-plan changed after pin |
| PACKET_HASH_MISMATCH | packet.md bytes != session.packet_hash |
| BUDGET_EXCEEDED | confirmation/rematch over configured limit |
| WRITE_POLICY_VIOLATION | plan/document production-apply, or impl misuse of human_approve |
| ILLEGAL_TRANSITION | transition not allowed from current state |
| PRECONDITION_FAILED | required artefact missing |

## Human approval

```bash
python3 scripts/workflow/approve.py --session DIR \
  --artifact PLAN.approved.md --approved-by alice
```

Chair / Zoro / Yonko / system are rejected as `approved_by`.
`PLAN.revised.md` alone never counts as approval.

## Human override (narrow)

```bash
python3 scripts/workflow/override.py --session DIR \
  --codes OPEN_MATERIAL_FINDINGS --reason "..." --approved-by alice
```

Does not bypass Evidence Index publish. Does not allow Chair self-approval.
Does not mutate or delete audit history. Visible in explain + efficiency report.

## Explain (read-only)

```bash
python3 scripts/workflow/explain.py --session DIR
```

Deterministic. No AI. No mutation. No replay.

## What this layer does NOT enforce

- Arbitrary filesystem edits Cursor cannot observe (documented limitation)
- Finding quality / engineering correctness
- Evidence Index publication (remains explicitly human-gated)
- Adaptive seating, prompts, routing, or auto-optimisation

## Compatibility

1. New V3.4 sessions: default `enforce`
2. V3.3 shadow artefacts: remain readable; stay shadow unless env override
3. Pre-workflow sessions: first observe initialises; history marked unknown

## Parked (not scheduled) - only if real friction demands it

Do not implement these from ambition:

1. Consolidate seat/budget/legality into `workflow-policy.yaml` as sole protocol contract
2. Protocol contract suite (states x transitions x guards x invariants)
3. Generate state diagrams from the contract (no hand-maintained graphs)
4. Explicit contract version matrix on sessions (workflow / IP / evidence / review policy)
5. Engineering assertions (`debug_assert!`-style impossible-state detectors), distinct from guards

Prefer deleting states and loosening over-strict rules after operational evidence.