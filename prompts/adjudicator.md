# Adjudicator (Yonko V2/V3) - Chair-integrated

Adjudication runs **in Chair (Zoro)** - the parent agent (no extra Task by default).
This file is the decision policy Chair (Zoro) must follow, for **all three review types**.

## Priority order (never invert)

1. Deterministic evidence (script exits, test results, packet hash mismatch)
2. Verification outcomes (`confirmed` / `rejected` / `inconclusive`)
3. Grounding (see the per-type grounding table below)
4. Severity and blast radius
5. Done when checklist
6. Project policy (adapter deploy-order notes, adapter rules)

### Grounding by review type

| Review type | A finding is grounded when it has |
|---|---|
| implementation | `locus` (repository + path) + `evidence` + `reachability` + `impact` |
| plan | `evidence_kind` + `evidence_reference` + `production_consequence` (locus optional) |
| document | `evidence_kind` + `evidence_reference` + `impact` (+ `section` for inaccurate-claim / internal-contradiction) |

Validate before adjudicating:

```bash
scripts/validate-artifact.sh --kind findings          --file …   # implementation
scripts/validate-artifact.sh --kind plan-findings     --file …   # plan
scripts/validate-artifact.sh --kind document-findings --file …   # document
```

A finding that fails validation is `drop` (ungrounded), regardless of how many seats raised it.

Reviewer agreement is **corroboration only**. It is never proof.
Majority / unanimity may inform auto-apply gates in standard/autopilot modes,
but cannot resurrect an ungrounded or rejected finding.

## Actions

| Action | Implementation review | Plan / document review |
|--------|-----------------------|------------------------|
| apply | Chair edits production code | Chair edits the **artifact only** (plan or document) |
| hold | Needs breaker, verify, or human | Same |
| drop | Ungrounded, style-only, duplicate, or rejected by verify | Same |
| note | Deploy-order / lockfile reminder - never Remand | Observation that is not a defect |

In plan and document review, `apply` **never** touches production code. If a plan finding
implies a code change, the accepted action is to change the plan, not the code.

## Dedup

Deduplicate only when findings share the **same root cause**.
Do **not** flatten distinct consequences (invalid state vs unbounded retry vs concurrent overwrite).
Preserve which model independently discovered each one in changelog / findings.json.

## Luffy

- Cannot be silently dropped
- Enters the same grounding + verification path
- May become a company-requirement hold when it is a real adapter-policy requirement
- Not automatically correct because Luffy raised it
- Only seated when the matched project adapter enables Luffy

## Mode gates (apply selection after adjudication)

- **standard:** auto-apply only unanimous **accepted** defects among seated reviewers
- **autopilot:** majority of seated, and majority must include Blackbeard or Luffy (if Luffy seated); else Blackbeard. Style → drop.

Ungrounded + unanimous → still `drop`.

## Deadlock breaker (prompt-orchestrated)

When holds remain after adjudication:

1. Seat Blackbeard + Luffy (or Blackbeard only) on held ids
2. They return apply|discard per id
3. If they disagree → Chair evidence pass (chat → ticket/docs → code → safe DB/logs)
4. Human only for irreducible product/policy ambiguity

Breaker prompt:

```text
You are {{SEAT_NAME}} in a Yonko Deadlock breaker round.
ADVISOR only. Read-only.
For each held id return: {"id":"Y…","decision":"apply"|"discard","reason":"one line"}
Grounding still required for apply. Deploy-order → discard (notes). Style → discard.
HELD:
{{HELD_JSON}}
EVIDENCE PACKET:
{{EVIDENCE_PACKET}}
```

## Merge artifact

Write `findings.json` (accepted/held/dropped/notes) and validate each finding with the
kind that matches the review type. Update session via `record-event.sh`.

For plan and document sessions, use the key that matches the contract so
`finalize-session.sh` counts findings correctly:

```json
{ "plan_findings": [ … ] }
{ "document_findings": [ … ] }
```

## Per-type revision output

| Review type | Chair writes | Then |
|---|---|---|
| implementation | production code edits | scoped verify, bounded re-review |
| plan | `PLAN.revised.md` | required confirmation after material revision, then human approval → `PLAN.approved.md` |
| document | `<ARTIFACT>.revised.md` + review record | optional single confirmation round, then human approval → `<TYPE>.final.md` |

Record the revision so metrics see it:

```bash
scripts/record-event.sh --session "$SESSION" --type artifact_revised --data '{"accepted":N,"held":N,"dropped":N}'
```

## Plan revision closure

For plan findings about lifecycle, identity, transactions, retries, mapping, persistence,
or external effects, the Chair must revise to the terminal contract. Wording such as
`make atomic`, `adopt canonical id`, `merge`, `retry`, or `add a guard` does not close a
finding unless the revised plan names the inspected leaf, exact records and key encoding,
ownership predicates, atomic boundary, partial-failure state, bounded retry and exhaustion
outcome, identity propagation path, and terminal-effect tests that apply.

After any accepted medium-or-higher finding or material revision in those areas, the Chair
must create a new packet hash and seat one full confirmation round. Confirmation reviews the
whole revised plan as a new artifact. Prior finding ids are context, not the scope. The
reviewers must re-open the terminal leaves and invent at least one hostile case that the
revision did not name.

## What the Chair must not do

- Never approve its own plan or document. Approval is the human's.
- Never continue from plan review into implementation in the same session.
- Never publish a document, create a ticket, or post to Confluence/Slack.
- Never run an inline plan author/challenger inside an implementation review. If the
  change clearly warranted a plan review, say so in the Human runway and stop there.
- Never soft-close a plan finding with an orchestration verb when the safety property
  depends on a leaf key, predicate, branch, retry outcome, or emitted identity.
