# Evidence vs execution profile - integration contract

Keep these concerns separate.

| Layer | Owns | Must not |
|-------|------|----------|
| Evidence Graph + Evidence Index | What must be reviewed; completeness; unresolved edges | Choose runtimes or models |
| Execution profile | Which runtime/model reviews the **already hashed** packet | Rebuild graph, re-query Index, rewrite completeness, change three-axis semantics |

## Required flow

```text
route reviewers
  → build local evidence graph
  → resolve cross-repo consumers (session co-collected signal match, then Index exact match)
  → evaluate completeness
  → assemble and hash packet
  → resolve / freeze execution profile
  → prepare seat dispatches (Cursor + OpenCode Task wrappers)
  → invoke independent seats (Cursor review Tasks and/or OpenCode via --execute)
  → validate findings
  → produce three-axis outcome
```

## OpenCode visibility (Cursor Task wrappers)

OpenCode reviews the packet; Cursor Tasks provide named tiles and parallel tracking.

1. Chair: `invoke-seat.sh --session … --seat …` (no `--execute`) → `dispatch.json`
2. Same turn: one Cursor Task per OpenCode seat (`task_description`, cheap Composer/Grok)
3. Wrapper Task: Shell-run `execute_command` (`--execute`); return status only

Do not background OpenCode from the Chair parent. Packet hash / completeness stay
invariant (see packet/profile invariance smoke).

## Hard rules for OpenCode / hybrid profiles

Seats consume the immutable packet (`packet.md` + `packet_hash`).

They must **not**:

- rebuild cross-repo evidence independently
- query the Evidence Index with a different algorithm than `evidence_graph/cross_repo.py`
- downgrade unresolved edges to covered
- invent another completeness calculation
- alter `review_outcome` / `evidence_completeness` / `deployment_recommendation` semantics

## Merge / rebase guidance

Rebase execution-profile work onto the Evidence Graph + three-axis + cross-repo tip
(3.8.0+). Do not hand-copy colliding files.

Likely collision surfaces: `VERSION`, `SKILL.md`, `state.py`, packet fingerprinting,
`finalize-session.sh`, `metrics.json` / `SUMMARY.md` writers, workflow phase scripts.

## Regression that must stay green

Same `packet_hash` and same `graph-completeness.json` cross-repo status must be
presented to Cursor and OpenCode seats under a hybrid profile. Provider selection
must not change the evidence under review.

Smoke: `scripts/test-packet-profile-invariance-smoke.py`
