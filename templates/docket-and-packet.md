# Docket + Evidence Packet templates (Yonko V2)

## Docket template (Chair writes; every seat receives)

Fill from **current chat + git** (+ files Chair read). Do not invent ticket ACs or specs. Write `none stated` when absent. Tag rare inferences as `inferred from diff (not stated in chat)`.

```markdown
# Yonko Docket

## Goal
<what we are shipping / reviewing for>

## Ticket / spec
- Ids: <TA-… / epic / none stated>
- Links: <MR/spec URLs or none>
- Acceptance / constraints from chat: <bullets>

## What we already did (this chat)
- <decision or change 1>

## Why
- <rationale / prior bug / review pressure / product constraint>

## Explicit constraints
- <e.g. no commit from Yonko; do not touch X; Java 21; …>

## Done when
- [ ] <behaviour or test outcome>
- Out of scope: <…>

## Golden path
- Symbol/path: <method or none stated>
- Steps: <3-8 short steps from code Chair read, or none stated>
- New change should: match | intentional diff: <what>

## Golden path excerpt
```
<≤ ~40 lines critical branch from Chair read, or none stated>
```

## Approved plan (V3/V4 handoff - omit the whole section if there was no plan review)
- Plan artifact: <path to PLAN.approved.md, or none>
- Originating plan session: <plan session id, or none>
- Deviations from the approved plan: <bullets, or none>
- Reason for each deviation: <bullets, or n/a>
- Packet note: with `--linked-session`, sanitise embeds scrubbed PLAN.approved.md only
  (decisions / risks / verification / evidence refs). Do not paste plan findings, prior
  packets, or planning dialogue into this Docket.

## Known gaps / open questions
- <from chat, or none stated>

## Request path
- <UI → … → service, or none stated>

## Domain terms
- <term>: <one-line meaning from chat, or none stated>

## Touch surface
- Repos/services: <paths>
- Expected DIFF labels (coverage): <exact labels seats must list in repos_reviewed>
- Key files named in chat: <paths>

## Diff map
```
repo: …
 files: …
 summary: …
```

## Proof aids
- Failing test snippet: <or none>
- Scoped verify cmd (planned): <or none>
- Prior bulletin summary: <or none>

## Rematch (if round > 1)
- Prior applies this session: <bullets>
- Held items still open: <bullets>
- Thrash watch: <titles applied before>

## Risk (from classify-risk.sh)
- Band: <trivial|low|medium|high|critical>
- Basis: diff-derived
- Reasons: <list>
- Plan review recommended (high/critical only, informational): <yes|no>
```

`recommend_plan_review: true` in `risk.json` is a **note for the human**. High/critical
implementation review does **not** run an inline plan author or challenger. If the change
warranted a plan review, say so in the Human runway and let the human run `/yonko plan`
next time. Never silently start one.

After `Log Pose locked.`, show a 5-10 line human summary. Full Docket goes to packet via `sanitise-and-hash-packet.sh`.

## Evidence Packet

Prefer building via script:

```bash
scripts/sanitise-and-hash-packet.sh --session "$SESSION" --docket /path/to/DOCKET.md
```

Manual shape (must match script output):

```text
=== YONKO DOCKET ===
...
=== REPOS ===
...
=== DIFF LABELS (must appear in repos_reviewed) ===
...
=== DIFF MAP ===
...
=== DIFF: <label> ===
...
```

Same packet for every seat. Never include other seats' findings mid-round.
Seats may read cited workspace files for verification, but findings must still cite the pinned packet hash / diff locus.
