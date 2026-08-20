# Changelog

Human-readable deltas for the Yonko skill. Runtime pin: `VERSION`.

## 3.10.3

- Org ship gate runs on **OpenCode Go** (`opencode-go/gpt-5.6-luna`).
  `run-org-ship-gate.sh` → `lib/run_org_ship_gate_opencode.py`.

## 3.10.2

- **Post-council org ship gate (fail closed when adapter-enabled):** after seats
  Content, Chair must run `scripts/run-org-ship-gate.sh` (hostile posture against
  the live tree) before `finalize --verdict pass` when `org_ship_gate` is enabled.
  Council Pass is not enough. Workflow codes `ORG_SHIP_GATE_REQUIRED` /
  `ORG_SHIP_GATE_FAILED`. Luffy remains the company-requirements seat only - not this gate.

## 3.10.1

- Fail-closed identity-source + reserved-key lifecycle audit
  (`scripts/lib/audit_identity_reserved_key.py`) with generic in-memory
  Fail→Pass acceptance (`scripts/test-identity-reserved-key-smoke.py`).
- Attack card rows: Identity sources in diff; Reserved-key lifecycle.
  Confirmatory "helper was called" without principal-vs-resource and
  stale-repair / batch-doomed-destination adversaries is Fail.

## 3.10.0

- Seat coach replaces explore hard-cuts: live `seat-status.json` + `progress.jsonl`
  visibility per OpenCode seat; soft no-progress / tool-cap / denied-loop triggers
  a coach nudge that continues the OpenCode `ses_…` session (`--session`) and
  pushes the model to emit findings JSON. Slow finishers that have started JSON
  keep the seat wall clock. Absolute timeout and frozen repair remain the safety
  net after nudge budget is exhausted.

## 3.9.10

- Replace the blunt explore time hard-cut with a streaming no-progress watchdog:
  cut only after soft budget + grace when no findings JSON has started; once
  findings text appears, keep the full seat wall clock so slow finishers are
  not killed. Wandering tool thrash past the tool-call cap (after soft budget)
  is also cut. Frozen recovery remains the safety net.

## 3.9.9

- Treat a full-seat explore burn before recovery as a flake: the explore turn
  now hard-cuts at soft budget + 60s grace (300s for high band), then frozen
  recovery runs on the remaining seat wall clock. Worst case is explore-cut +
  recovery, not 600s + recovery.

## 3.9.8

- Stop hard-killing OpenCode at the exploration `max_duration_seconds` soft
  budget. That clamp left no room to emit findings and made frozen recovery
  race the same 240s wall clock (observed: confirmation arm 3/5 with empty
  stdout timeouts on both attempts).
- Frozen timeout recovery now uses a dedicated seat-derived budget
  (`max(240, min(seat_timeout, 420))`) and accepts findings from stdout or the
  written output file.

## 3.9.7

- Repair and timeout recovery now always run frozen: the re-invoke gets
  `packet_only` permissions and prompt, so a seat that spent its turn on tool
  calls converges to findings JSON instead of exploring again.
- A `packet_plus_workspace_read` seat that exhausts its exploration wall clock
  gets one frozen recovery turn before the seat is failed. Recovered seats
  report `recovered_from_timeout`.
- Deny `bash` in exploration mode. Rejected shell calls consumed whole turns and
  the Packet already carries the diff.
- Exploration prompt gains a convergence hard law: a tool-call cap derived from
  the frozen budget, never end a turn with a tool call, never retry a rejected
  tool.
- `_run_opencode_once` now forwards `env` and `timeout_sec` to the runner, so
  per-attempt permissions take effect.
- New `test-exploration-reliability-smoke.py` pins tool-only turns, timeout
  recovery, frozen repair permissions, and bounded repair.

## 3.9.6

- Harden OpenCode `packet_only`: deny the `read` tool (not only glob/grep/list/LSP)
  so large-packet reviews cannot burn the turn on rejected workspace reads.
- Harden missing-JSON handling: when OpenCode emits no extractable findings JSON,
  run one bounded repair re-invoke instead of hard-failing immediately.
- Strengthen `packet_only` and repair prompts: no tools; emit findings JSON or an
  empty findings array.
- Raise high-band exploration `max_duration_seconds` from 180 to 240.

## 3.9.5

- Attack cards: hard-fail for accumulated external
  side-effect compensation (BUG C) - sequential remote applies tracked in a
  local accumulator must enumerate every exit path and prove compensation;
  database rollback is not cover.
- Live OpenCode reviewers now use `packet_plus_workspace_read`: the immutable
  Packet remains authoritative while declared workspace repositories are
  available through read, search, glob, and Language Server Protocol (LSP)
  tools.
- A dedicated `yonko-reviewer` agent denies edits, subagents, network tools,
  arbitrary shell, sensitive paths, branch changes, commits, pushes, and
  sibling private policy trees. `--auto` remains forbidden.
- Exploration mode, workspace root, and risk-band budget are frozen into each
  OpenCode invocation. Budget overruns fail closed.
- Per-seat discovery ledgers record files, searches, LSP lookups, bytes, time,
  and truncation. Finalization emits suggest-only packet-omission candidates and
  the miss taxonomy for Evidence Graph evaluation.
- Frozen-packet replay forces `packet_only`; full-pipeline replay preserves
  workspace discovery.
- Attack cards now hard-fail count-then-act lock-scope mismatch
  and success-returning catches after transaction rollback. BUG A and BUG B are
  the canonical regressions.
- Regression: `scripts/test-opencode-workspace-exploration-smoke.py`.

## 3.9.4

- Return / DTO population semantic changes are classified
  (`public_return_population_change` / `dto_field_population_change`) and in-repo
  readers are staged into the packet under `=== IMPACT READERS ===`.
- Missing readers emit unresolved edges that name the affected symbol.
- `outcome.json` adds `clean_pass_allowed` and `presentation.headline`. Incomplete
  evidence with unresolved `operational_side_effects` or `cross_repository_consumers`
  cannot headline sole Pass / push-ready / clean.
- `finalize-session.sh` clamps Chair `--confidence` to the outcome ceiling and prints
  the presentation headline on SUMMARY / Human runway.
- Completeness gates treat `operational_side_effects` as material when touched
  (medium+ blocks complete verdict).
- Plan and implementation Attack cards require enumerating readers when returned-field
  population semantics change.
- Regression: `scripts/test-return-reader-packet-smoke.py` +
  `scripts/fixtures/evidence-graph/mini-spring-readers/`.

## 3.9.3

- Plan reviewers must follow lifecycle, identity, mapper, retry, persistence, and
  external-effect steps to the terminal leaf instead of accepting orchestration verbs
  such as `atomic`, `adopt`, `merge`, or `retry` as proof.
- Plan Attack cards now require exact records and key encoding, ownership predicates,
  atomic boundaries, retry exhaustion, mapper authority, canonical identity propagation,
  one conflict policy for online and repair paths, terminal-effect tests, and measurable
  operational acceptance.
- A full hostile confirmation round is mandatory after accepted medium-or-higher plan
  findings or material leaf-contract revisions. It reviews the whole revised plan and
  invents new cases instead of only checking prior finding ids.
- `artifact_revised` no longer consumes the confirmation budget. The workflow counts a
  plan confirmation only when the revised packet seats reviewers with
  `confirmation_round:true`, and blocks Pass when a required confirmation did not run.

## 3.9.0

- Luffy (cursor-opencode-go) default: `opencode-go/qwen3.7-plus` (was Kimi K3; 600s plan timeouts). Kimi kept as `luffy_kimi` alternate.
- OpenCode kickoff: parent `seat-council --prepare --with-kickoff` / `--kickoff` detaches `--execute` immediately (wrappers are visibility only; Cursor Task starvation was the root "not starting" bug)
- `--execute-awaiting` recovers never-started / abandoned seats only; live `execute.pid` / `kickoff.pid` skipped; SIGTERM is not success
- Garbage `findings.json` (e.g. NDJSON `step_start`) no longer counts as `has_findings`

- Observational evaluation system: shared capture after metrics/confidence/outcome
- Canonical `evaluation/review-measurement.json`; ledger is a backward-compatible projection
- Council effectiveness, eval corpus (human promote), frozen/full replay, ground truth / escapes, suggest-only proposals
- `observability-policy.yaml` owns `evaluation.capture_on_finalize` / `fail_open`; `evaluation.yaml` owns paths/`min_sample_n`
- No auto-promote or CI-gate config keys; no auto-tune; replay never mutates `execution-profile.json`

## 3.8.2

- OpenCode seats: Cursor Task wrappers (dispatch → `--execute`); Shell-only; no Edit Allow spam from seats
- Tile titles include real OpenCode model; Cursor badge still shows wrapper model
- Docs inventory refresh: Flash default (Pro alternate), three-axis in DOCUMENTATION, session/script trees

## 3.8.1

- OpenCode default path is dispatch-first (`awaiting_chair_dispatch` / `cursor_task_wrapper`)
- `invoke-seat.sh --execute` runs the OpenCode CLI from the wrapper Task

## 3.8.0

- Evidence Graph cross-repo consumers: Evidence Index **exact** api/event/contract match
- `hasAuthority` remains candidate-only (not covered)
- Packet/profile invariance smoke
- Three-axis outcome model wired through finalize (`outcome.json`)

## Earlier

See git history and `docs/plans/` for Evidence Graph / profile design drafts. Papers under
`papers/` describe protocol thesis and may lag harness versions.
