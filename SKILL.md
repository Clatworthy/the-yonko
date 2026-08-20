---
name: the-yonko
description: >-
  Runs Consult the Yonko / Yonko autopilot: multi-model engineering review round table.
  V3 supports three review types - plan review (before code), implementation review
  (diffs, unchanged from V2), and document review (PAP / PRD / ADR / technical design).
  Seats Chair (Zoro) as sole writer; independent reviewers Shanks, Blackbeard, Buggy,
  Luffy (company-specific requirements, adapter-gated). Models/runtimes follow the active execution profile
  (Cursor-only or cursor-opencode-go). Never commits or publishes.
  Triggers: Consult the Yonko, Yonko autopilot, yonko, consult yonko, seat the Yonko,
  /yonko, /yonko plan, /yonko review, /yonko full, /yonko document,
  /yonko evidence publish, /yonko explain, /yonko improve, /yonko doctor.
---

# Consult the Yonko

> An evidence-driven engineering council that plans, reviews, documents and preserves institutional engineering knowledge.

![Yonko Council: Chair (Zoro) codes while Blackbeard (correctness), Shanks (architecture/contracts), Buggy (chaos), and Luffy (company-specific requirements) review - Plan it. Prove it. Preserve it.](assets/yonko-council.jpg)

*Plan it. Prove it. Preserve it.*

**Counter to vibe coding.** Generate-glance-ship is the industry default when AI writes
most of the code. Yonko introduces standards and guardrails: packet before opinion,
independent critics before merge, script harness that can fail closed, human authority on
commit/publish. Same agents. The first green path is not a pass.

Light ceremony. Cold findings. **Chair (Zoro)** holds the quill. The reviewing Yonko do not touch the code.

**Philosophy:** Models discover. Scripts enforce. Evidence grounds. Verification proves. Chair (Zoro) integrates. Humans decide.

**Protocol governs process. Evidence governs decisions.**  
**Invariants:** `docs/INVARIANTS.md` (stable). Do not invent protocol features without clearing the feature gate there.

**Core:** shared packet, independent council, deterministic routing, verification separate from judgement, workflow legality (`enforce` by default; `YONKO_WORKFLOW_MODE=shadow` for diagnosis), human authority.

**Optional:** Evidence Index (`/yonko evidence publish`), continuous improvement (`/yonko improve`, suggest-only), efficiency reporting on finalize.

**No optimisation without evidence:** metrics inform humans; they must not silently change seating, packets, retrieval, or apply rules. Packet optimisations must pass dual-packet quality acceptance (`V4.md`, maintainers).

One skill, one shared council engine, three explicit review types. There is no fused
automatic pipeline: each type is a separate session the human starts deliberately.

This is **prompt-orchestrated** with **script hard gates**. It is not a graph runtime.

| Mechanically enforced (scripts) | Prompt-orchestrated (Chair / Zoro) |
|---------------------------------|-----------------------------|
| Git evidence, secret scrub, packet hash/version | Routing, seating, independence |
| Risk band + reasons (diff-derived or scope heuristic) | Adjudication, bounded loops |
| Finding / verification / verdict structural validation, per review type | Single-writer, human escalation |
| Append-only `events.jsonl` / `session.json` via scripts | Invoking the scripts in order |
| Workflow legality (pin freshness, seats, verify presence, open material disposition, human approval metadata, budgets, write fences) | Finding quality and remediation judgement |
| Scoped build/test exit codes when run | Full-scope review quality |
| Handoff-artifact presence at finalize | Whether the artifact is any good |

---

## Route the invocation first

| Invocation | Review type | Artifact under review | Ends with |
|---|---|---|---|
| `/yonko plan` | **plan** | a drafted implementation plan | `PLAN.approved.md` after human approval |
| `/yonko` or `/yonko review` | **implementation** | the working-tree / branch diff | applied fixes + Human runway |
| `/yonko full` | **implementation**, forced high route | the diff | same as above |
| `/yonko quick` | **implementation**, forced low route (safety floor still applies) | the diff | same as above |
| `/yonko autopilot` | **implementation**, autopilot apply gate | the diff | same as above |
| `/yonko document pap\|prd\|adr\|design` | **document** | a PAP / PRD / ADR / technical design | `<TYPE>.final.md` after human approval |
| `/yonko evidence publish` | **completion adapter** (not a review) | a finalized session | local canonical record after explicit hash approval |
| `/yonko improve` | **continuous improvement** (not a review) | Evidence Index checkout | Engineering Improvement Suggestions (suggest only) |
| `/yonko doctor` | **setup diagnostic** (not a review) | active execution profile | pass/fail checks (no secrets, no paid inference) |
| `/yonko explain` | **workflow explain** (not a review) | a session directory | legality + routing dump (read-only) |

Natural language still works: `Consult the Yonko` → implementation, `Yonko autopilot` →
autopilot, `review this plan with the Yonko` → plan, `Yonko review this PRD` → document.

**Breaking rename (V3):** `/yonko plan` used to force the high implementation route. That
is now `/yonko full`. `classify-risk.sh --force plan` exits with an error pointing here.

If you are about to run a single-repo review bot because the user said Yonko: **wrong**.
This skill is the one. Luffy (when seated) applies company-specific engineering
requirements only - house rules you plug in via the adapter.
**Org ship-gate parity is step 15b** when the matched adapter enables `org_ship_gate`
(OpenCode Go / `opencode-go/gpt-5.6-luna` hostile gate) - not Luffy, not Chair self-review.

Skill root: `~/.cursor/skills/the-yonko/`. Sessions: `~/.cursor/yonko-sessions/`.
Companions: `DOCUMENTATION.md`, `SHARE.md` (install + adapters), `ENGINEERING-PATTERNS.md`,
`ARCHITECTURE.md`, `V4.md`, `config/`, `prompts/`, `scripts/`, `contracts/`, `templates/`.

**Adapters:** read `config/project-adapters.yaml`, then merge
`config/project-adapters.local.yaml` if present (gitignored local overlay). Seat Luffy
only when the matched adapter has `luffy.enabled: true` (high/critical escalation band,
or `/yonko full`). **Luffy is not the org ship gate.** When the matched adapter enables
`org_ship_gate`, after council Content, Chair must run the post-council org ship gate
before any `finalize --verdict pass` - see step 15b below.

### Execution profiles (runtime selection)

**Recommended:** `cursor-opencode-go` (Cursor Chair/Shanks + [OpenCode Go](https://opencode.ai/docs/go/) for Blackbeard/Buggy/Luffy). Best cost/volume for frequent Yonko - Go seats do not burn Cursor external-API credits. See `docs/EXECUTION-PROFILES.md` and `docs/providers/OPENCODE-GO.md`.

Profiles choose runtime/model per seat; they do **not** change risk band or seat count.
Missing marker falls back to `cursor-standard` (no OpenCode required).

**`cursor-opencode-go` ladder** (when seated): Chair/Auto → Shanks/Grok → Blackbeard/DeepSeek V4 Flash → Buggy/GPT-5.6 Luna → (if required) Luffy/Qwen 3.7 Plus. Model IDs: `config/model-selections.json`. No Opus/Sol Remand ladder on this profile.

```bash
scripts/set-execution-profile.sh --profile cursor-standard|cursor-opencode-go|cursor-max
scripts/yonko-doctor.sh          # or /yonko doctor
scripts/invoke-seat.sh --session DIR --seat blackbeard
```

Marker: `config/execution-profile.json`. Frozen into each session at init.
OpenCode seats receive the same immutable Packet as their authoritative starting
evidence. Live `cursor-opencode-go` reviews use `packet_plus_workspace_read`: the
seat may read, search, and use Language Server Protocol (LSP) navigation across
the declared workspace to verify or extend Packet evidence. Edits, subagents,
network access, package installation, branch changes, commits, and pushes are
denied. Every explored file/search/LSP lookup is recorded under
`runtime/<seat>/repository-exploration.json`. Frozen-packet evaluation replay
forces `packet_only`.
No silent fallback. No implementation-phase subagents in this layer.

When seating reviewers, Chair dispatches via `invoke-seat.sh` (provider-neutral).
Cursor seats return a Chair Task dispatch artefact.
OpenCode seats also return a Chair Task dispatch by default (`cursor_task_wrapper`);
the wrapper Task must Shell-run `invoke-seat.sh --execute` as its **first** tool call so
OpenCode starts immediately while Cursor shows a named tile. Spawn OpenCode wrappers
before Cursor seats; use the ~20s `never_started` watchdog / `--execute-awaiting` if a
wrapper tile starves.


---

## Engineering Evidence Index (completion adapter, V3.1)

Opt-in institutional memory of **completed** Yonko sessions. Not a fourth review type.
Does not change seating, risk, adjudication, or apply rules.

- Config: `config/evidence-index.yaml` (+ `.json`), taxonomy under `config/evidence-taxonomy/v1/`
- CLI: `scripts/evidence-index.py` (`candidate`, `validate`, `publish-local`, `append-event`, `rebuild`, `refresh-cache`, `query`)
- Local canonical checkout: set `YONKO_EVIDENCE_REPO` (run `init-repo` once). Unset = adapter idle.
- After finalize: eligibility is reported only.
- Preferred UX: `/yonko evidence publish` → `scripts/evidence-index.py publish` (candidate → preview what will be indexed → validate/secret-scan → explicit `--confirm-hash` + `--approved-by` → `publish-local`).
- Low-level: `candidate` then hash-confirmed `publish-local` still work.
- Never auto-commits, pushes, or contacts a remote.
- After publish (optional): `/yonko improve` → `scripts/continuous-improvement.py analyze`. Pattern analysis over the Evidence Index. Emits **Engineering Improvement Suggestions** only (isolated bug vs process signal). Never rewrites SKILL, routing, prompts, or workflow. Human decides.
- Before a future plan/document draft: optional `query` → show retrieval receipt → add selected ids as `informed_by`.

Templates: `templates/evidence-candidate.md`, `templates/evidence-retrieval-receipt.md`, `templates/engineering-improvement-suggestions.md`.

---

## Invariants (all three review types)

1. All reviewers receive the **same** neutral core packet.
2. Every reviewer assesses the **complete** artifact.
3. Specialist lenses are attention biases, **never** boundaries. Do not partition.
4. Reviewers do not see each other's findings before adjudication.
5. Only **Chair (Zoro)** writes or revises the artifact.
6. Evidence outweighs votes. One strong verified finding can block approval.
7. No automatic commit, push, publication, or continuation into implementation.
8. Human approval is the final gate.
9. Scripts claim only what they genuinely enforce.

| Seat | Family | Extra attention |
|------|--------|-----------------|
| **Chair (Zoro)** | Parent on **Composer** (first-party) when possible | Docket, scripts, adjudication, revision, verify, bulletin - final decision and only one who codes |
| **Shanks** | OpenAI **Luna** (API pool; then Terra/Sol) | contracts, compatibility, requirements |
| **Blackbeard** | **DeepSeek** when on Task allowlist, else Claude Sonnet | correctness, concurrency, side effects |
| **Buggy** | **Grok** (first-party; required) | chaos / adversarial cases |
| **Luffy** | OpenCode Go: **Qwen 3.7 Plus**; Cursor profiles: prefer `qwen*` / Composer (adapter-gated) | Company-specific requirements; abroad until a local adapter enables him |

Zoro is the Chair persona. Task descriptions for reviewers still start with `Shanks` / `Blackbeard` / `Buggy` / `Luffy`. The parent agent **is** Chair (Zoro) - do not spawn a separate Zoro Task.

### Dynamic model selection (shared)

Resolve from the **current** Task allowlist each invoke. Keep the roster stable across
rounds. Defaults are **Pro+ pool-aware** (see `config/model-policy.yaml`):

- Chair parent: prefer **Composer** (first-party), else Grok
- Shanks `gpt*` - prefer **luna > terra > sol**, and **medium/fast** before thinking/high
- Blackbeard `deepseek*` when listed, else `claude*` - prefer **deepseek/flash > sonnet > fable > opus**
  (never silent Grok/Composer as Blackbeard)
- Buggy `grok*` **required**, no silent substitute (first-party pool)
- Luffy: Go panel uses Qwen; Cursor profiles prefer `qwen*` then **composer** before burning another GPT/Claude

**If DeepSeek is not on the Task allowlist:** seat Claude Sonnet for Blackbeard and say so in
the roster. Do not BYOK. Do not invent a `deepseek*` slug.

**Budget stewardship:** spread seats across first-party (Composer/Grok) and API (Luna/DeepSeek)
pools. If Blackbeard's family (deepseek|claude) is entirely unavailable, report Blackbeard
unavailable and do not pretend the council is full.
Escalate to Opus / Sol / thinking-high only on human request or Remand/deadlock rematch.

### External seats (Claude Code / Codex) when Cursor Other Models are dead

**Human never runs this path.** Chair (parent agent) Shell-invokes it.

**Hard trigger (non-optional):** as soon as a Task for **Blackbeard** or **Shanks** fails to start
or returns unusable (`Couldn't start`, spawn error, API / on-demand / Other Models capped,
empty result with no findings file), Chair must **immediately** run the external seat for that
seat - do **not** wait for Buggy/Luffy to finish, do **not** ask the human to run a CLI, do
**not** substitute Grok/Composer as Blackbeard or Shanks.

Note: external CLI today is **Claude Code** (Blackbeard) and **Codex** (Shanks). If Blackbeard
was seated as DeepSeek in Cursor and that Task fails, external Claude Code is still the honest
correctness fallback - announce `blackbeard via claude-cli (deepseek Task failed)`.

**Budget rules for external CLIs:**

- **No smoke tests** via Claude Code / Codex (`ok` probes). Health checks stay on **Cursor Models** (Grok / Composer).
- Export a **slim packet** (Docket + DIFF LABELS + DIFF MAP + DIFF hunks only). Drop REPOS paths, CHANGE CLASSES, REVIEWER ROUTING.
- CLI prompts are **paths only** - never inline `SEAT.md` or the packet into the argv (duplicate tokens).

Chair runs (Shell, same session dir):

1. Prefer end-to-end (exports then invokes; company Claude / Codex auth - not Cursor credits):
   `scripts/run-external-seat.sh --session DIR --seat blackbeard|shanks`
   - Blackbeard → `claude -p` (Claude Code CLI)
   - Shanks → `codex exec` (Codex CLI / `npx @openai/codex`)
2. On success: `SESSION/external/<seat>/findings.json` - Chair validates and adjudicates like any seat;
   record `task_call` with `model` set to the CLI identity (e.g. `claude-cli` / `codex-cli`).
3. If the CLI binary is missing or the invoke fails: Chair leaves `SEAT.md` in place, reports the
   seat as **external-blocked** in chat (facts only), and continues adjudication with an honest
   incomplete council - still never a silent Grok swap. Only then may Chair ask the human whether
   to install/auth the CLI or accept the incomplete roster.

Announce external seats in the roster when used. Confidence stays honest if a required seat only
lands via external channel or is blocked.

---

## 1. Implementation review (V2 behaviour - unchanged)

Loop: `review → adjudicate → apply → verify → bounded re-review`

```text
0. Mode + optional force route from invoke
1. scripts/init-session.sh --mode … [--force-route …]            # --type defaults to implementation
2. Mine chat → write DOCKET.md (templates/docket-and-packet.md)
3. scripts/collect-evidence.sh --session … --repo … [--workspace …]
4. scripts/classify-risk.sh --session … [--force …]
4b. scripts/classify-change.sh --session … [--advisory class1,class2]
4c. scripts/route-reviewers.sh --session …
4d. scripts/build-evidence-graph.sh --session …
   (writes evidence-graph.json + graph-completeness.json; exit 3 blocks seating unless --waive)
5. Context pack lint (prompt) → scripts/sanitise-and-hash-packet.sh --session … --docket …
5b. Resolve / freeze execution profile (runtime only - must not mutate packet or Evidence Graph)
6. Deterministic checks if applicable (compile/test/lint - exit codes)
7. Seat reviewers per evidence/routing.json (prompts/reviewers.md) - **parallel**:
   - `scripts/seat-council.sh --session … --prepare --with-kickoff` (dispatch + **parent
     detaches OpenCode --execute immediately**)
   - Same Chair turn: spawn Tasks from `task_spawn_order` - OpenCode wrappers first
     (`run_in_background: true`, Shell `execute_command` joins if already running), then
     Cursor seats
   - ~20s watchdog: any OpenCode `never_started` / `abandoned` → `--kickoff` again
   - Cursor runtime Tasks: review packet with resolved Cursor model (existing behaviour)
   Identical `packet_hash` for every seat/runtime.
   Record reviewers_seated with seats[] matching routing.json exactly. Optional focus_hints are
   attention bias only - never review boundaries.
   After **each** Task/subagent (or external seat) returns, record a `task_call` event with the
   actual model slug used, e.g.
   `record-event.sh --session … --type task_call --data '{"seat":"shanks","model":"gpt-5.6-luna-medium","count":1}'`.
   Also increment `session.subagent_calls` (or rely on task_call counts). Seat **budget** from
   routing is not consumption - incomplete sessions with seats=4 and calls=0 are not four-model spend.
   **If Shanks/Blackbeard Task shows Couldn't start / spawn fail:** Chair Shell-runs
   `run-external-seat.sh` for that seat immediately (see External seats). Never ask the human to.
```

### Chair seat-invoke hard laws (latency / process failure if broken)

These are non-negotiable. Broken invoke is why humans sit waiting for "stuck" OpenCode.

1. **Announce before seating:** risk band, seat list, packet bytes, and honest ETA
   (OpenCode seats are typically 1-3+ minutes each; they run in parallel, not instant).
2. **Parent starts OpenCode (mandatory):** after `seat-council.sh --prepare`, immediately run
   `seat-council.sh --session DIR --kickoff` (or `--prepare --with-kickoff`). That detaches
   `invoke-seat.sh --execute` per OpenCode seat from the **parent** - do **not** wait for
   Cursor Task wrappers to schedule. Wrapper starvation is a known Cursor failure mode
   (tile "running", zero tool calls, `attempts: 0`).
3. **Wrappers stay for visibility only.** Spawn named Cursor Task wrappers from
   `task_spawn_order` (OpenCode first, `run_in_background: true`). Prefer
   `council.json` `wrapper_prompt`. First tool = Shell `execute_command` (joins if already
   running). No `UpdateCurrentStep` / Read before Shell. `required_permissions: ["all"]`.
   Never pipe through `head` / `tail`.
4. **Watchdog ~20s:** `seat-council.sh --status`. Any OpenCode `never_started` /
   `abandoned` → `--kickoff` or `--execute-awaiting` again. Do not wait minutes.
5. **Do not AwaitShell-poll OpenCode in the parent** for the full timeout before dispatching
   Cursor seats. Kickoff first; spawn wrapper + Cursor Tasks; wait on `result.json`.
6. **Do not tell the human OpenCode is broken** when doctor is READY and a prior seat
   completed - empty logs usually mean Chair never ran `--kickoff`, or starved/killed the wrapper.
7. Packet already uses a **compact Evidence Graph** in `packet.md` (full JSON stays under
   `evidence/`). Do not re-paste `evidence-graph.json` into seat prompts.
8. **No human Allow spam from seats:** Cursor and OpenCode-wrapper Tasks must not call
   Edit/Write/StrReplace. If Shell asks Allow, that is Cursor auto-run settings.
9. **Protocol freeze:** while a Yonko session is in progress (`session.json` not finalized),
   do **not** edit `SKILL.md`, `scripts/`, or `config/` to "make this
   review faster." Protocol / launcher fixes are a **separate** change with smokes first.
10. Before bulletin or finalize: run `scripts/seat-council.sh --session DIR --require-complete`.
    Incomplete OpenCode seats → failure code `OPENCODE_EXECUTE_MISSING`.

```text
8. scripts/validate-artifact.sh --kind findings --file …
9. Normalise / dedup by root cause (Chair)
10. Verify material findings if routing.require_verifier or route requires (prompts/verifier.md)
11. Adjudicate (prompts/adjudicator.md) - evidence > votes
12. Chair applies accepted fixes only
13. Scoped verify (exit codes) → record-event
14. Round bulletin (templates/bulletin-and-verdict.md)
15. Rematch within budget | Pass | Deadlock path | Adjourned
15b. **Org ship gate (adapter-gated implementation Pass - fail closed when enabled):**
    When the matched adapter has `org_ship_gate.enabled: true`, seats are Content, and
    Chair would Pass, **before** Engineering Confidence / `finalize-session.sh --verdict pass`:
    1. Announce: `Org ship gate (hostile, OpenCode Go / GPT Luna) - council Pass is not enough.`
    2. Run:
       `scripts/run-org-ship-gate.sh --session DIR`
       (OpenCode Go model **`opencode-go/gpt-5.6-luna`** against the **live working
       tree**, policy files from adapter `org_ship_gate.skills`, as if you did not
       implement the change).
    3. If `opencode` is missing: stop with `ORG_SHIP_GATE_REQUIRED` and tell the human
       to install/auth OpenCode Go - or `--export-only` then `--import-result` only as
       a last resort.
    4. Gate Remand / non-empty findings / missing Attack card → **do not Pass**.
       Fix or Remand; re-run the gate after fixes.
    5. `finalize-session.sh --verdict pass` is blocked by workflow legality
       (`ORG_SHIP_GATE_REQUIRED` / `ORG_SHIP_GATE_FAILED`) until
       `SESSION/org-ship-gate/result.json` Validates Pass.
    If the adapter does not enable `org_ship_gate`, skip 15b.
    Skipping 15b when it is enabled because "Yonko already agreed" is a **process failure**.
16. On final stop: Engineering Confidence (chat) + scripts/finalize-session.sh
```

Announce: `The Yonko take their seats.` + roster from `routing.json` + risk band + packet hash prefix.
`/yonko explain` prints workflow legality **and** Selected reviewers with reasons.

### Hard fails Chair must not rubber-stamp (implementation)

These are Fail even when seats return empty findings. Chair remands or invents the finding.

1. **Reconstructed outbound drops sibling inbound fields** - building a redirect,
   Location, callback URL, or similar from a split request model (path vs query vs
   fragment vs host, etc.) without preserving fields the change did not intend to
   alter. Require a leaf test with non-empty sibling fields. Prefer an Attack-card style
   review of leaf channel/identity (or your org's equivalent adversarial gate).
   `Reconstructed outbound preserves sibling inbound fields`.
2. **Vendor/runtime event shape vs fixture** - code that reads or re-emits a
   platform/SDK/vendor event field must use fixtures in the **documented
   production type** (object map / nested value / multi-value, not a simplified
   string stand-in). Green "preserve X" tests against the wrong type are Fail.
   Section 1c does not cover this. See section 1d and Attack card row
   `Vendor/runtime event shape vs fixture`.
3. **Vendor doc / sample cite required** - when (2) applies, the Attack card must
   include an exact vendor URL or in-repo sample path. "Per the docs" / memory
   without a cite is Fail. See Attack card row `Vendor doc / sample cite`.
4. **Hostile re-review of preserve/serialize fixes** - a patch that "fixes"
   dropped or mis-serialized inbound fields must be re-attacked as a new change
   (sections 1c/1d/1e). Prior-round Pass and co-authored unit greens are not
   cover. Author-Chair `findings: []` without cite + invented extra case is Fail.
5. **DIFF MAP path with no packet hunk** - new/untracked files must appear in the
   patch (`collect-evidence.sh` synthesises untracked diffs). Seats must open
   mapped paths that still lack hunks before clearing concerns on those files.

### Risk routes and Task budgets (diff-derived, from `config/risk-policy.yaml`)

| Risk | Baseline reviewers | Verify | Target Tasks |
|------|-----------|--------|--------------|
| trivial | 1 | no | 1 |
| low | 2 (different families) | no | 2 |
| medium | 3 | disputed/material | 3-5 |
| high | 4 + Luffy (when adapter enables) | high/critical | 8-10 |
| critical | 4 + Luffy (when adapter enables) | high/critical | 10-12 |

**V3.5:** `config/routing-policy.yaml` unions change-class seats onto the band baseline.
Scripts own classification; Chair may only add `--advisory` tags from the closed enum.
AI never invents seats. Plan/document review still uses scope-band seating (unchanged).

**V3 change:** high/critical no longer runs an inline plan author and challenger. When
`risk.json` shows `recommend_plan_review: true`, say so in the Human runway - "this change
warranted `/yonko plan` before implementation" - and **stop there**. Never silently start a
plan review inside an implementation session.

### Approved-plan handoff (when a plan session preceded this)

Pass `--linked-session <plan-session-id>` to `init-session.sh`, and fill the
`## Approved plan` block in the Docket: plan artifact path, originating plan session,
deviations from the approved plan, and the reason for each deviation. The approved plan
then counts as Done when evidence for this review.

**Handoff boundary (V4 Phase 1):** `sanitise-and-hash-packet.sh` stages
`evidence/approved-plan.md` from the linked session's `PLAN.approved.md` and embeds it
in the packet. Do **not** replay the plan session's findings, rejected findings, prior
packets, or planning dialogue into the implementation packet.

---

## 2. Plan review (`/yonko plan`)

Reviews a **proposed implementation plan** before any code exists. Never writes code.

Loop: `review → revise → risk-triggered single confirmation round → human approval`

```text
0. The human has pasted a ticket and Cursor has drafted a plan in this chat.
1. Save the plan to a file (e.g. PLAN.draft.md) and write reconnaissance notes (RECON.md):
   the repository paths and symbols you actually opened while drafting.
2. scripts/init-session.sh --type plan --mode … [--linked-session …]
3. Write the Plan Docket (templates/plan-review.md)
4. scripts/collect-plan-evidence.sh --session … --plan PLAN.draft.md \
     [--source ticket.md] [--recon RECON.md] [--repo <abs>]…
5. scripts/classify-scope-risk.sh --session …
6. scripts/sanitise-and-hash-packet.sh --session … --docket …/PLAN_DOCKET.md
7. Seat reviewers per scope band (prompts/plan-reviewers.md) - parallel Task, identical packet
   Record `task_call` with model slug after each seat (same as implementation).
   Shanks/Blackbeard Task fail → Chair runs `run-external-seat.sh` immediately (human never runs it).
8. scripts/validate-artifact.sh --kind plan-findings --file …
9. Normalise / dedup by root cause (Chair)
10. Verify material findings (prompts/verifier.md - checks the citations are real)
11. Adjudicate (prompts/adjudicator.md) - evidence > votes
12. Chair writes PLAN.revised.md - the PLAN only, never production code
    scripts/record-event.sh --session … --type artifact_revised \
      --data '{"accepted":N,"accepted_medium_or_higher":true|false,"material_leaf_revision":true|false}'
13. Round bulletin (templates/plan-review.md)
14. Run ONE confirmation round on the revised plan when any accepted medium-or-higher
    finding or material lifecycle / identity / transaction / retry / mapper / persistence /
    external-effect revision exists (new packet hash, same full seat set). Record the
    `reviewers_seated` event with `"confirmation_round":true` so the workflow budget and
    required-confirmation guard apply.
15. Engineering Confidence + scripts/finalize-session.sh --verdict …
16. Human runway: ask for approval. STOP.
```

**Stopping conditions:** one confirmation round maximum. The confirmation round is mandatory
after the material revisions listed in step 14. It is optional only when the first round
accepted no material finding and made no material revision. Plan review **never** continues
into implementation, even in autopilot. The session ends with a plan the human approves or
rejects. `PLAN.approved.md` is written only after the human approves - the Chair never
approves its own plan.

**Risk:** `classify-scope-risk.sh` produces a band labelled
`heuristic from stated scope and inspected context`. It is **not** equivalent to
diff-derived risk: it reads what the plan says and cannot see what the plan omits. State
that basis in the bulletin. Reviewers are explicitly instructed to hunt omitted scope.

| Scope band | Reviewers | Target Tasks |
|---|---|---|
| trivial / low | 2 (different families) | 2-3 |
| medium | 3 | 4 |
| high | 4 + Luffy | 6 |
| critical | 4 + Luffy | 7 |

**Grounding:** a concrete code locus is **not** mandatory, because the most valuable plan
findings concern something absent. Grounding is still strict - every finding declares
`evidence_kind` (`plan_section` / `code_inspected` / `contract_inspected` /
`document_inspected`) plus a real `evidence_reference` and a `production_consequence`.
Validation rejects `n/a`, `tbd`, and `code_inspected` references with no path.

### Plan leaf-contract hard fails

These are Remand even when seats return Content. Chair must not accept high-level plan
verbs as implementation contracts.

1. A material create, rename, archive, adopt, claim, merge, repair, map, retry, or external
   effect does not name the terminal persistence or external-effect leaf inspected.
2. A multi-record lifecycle change says `atomic` without naming the exact records, key
   encoding, ownership predicate for each mutation, transaction boundary, and partial-failure
   state.
3. A conditional or optimistic write has no bounded conflict retry and no explicit
   retry-exhausted outcome, metric, and test.
4. A mapper or resolver does not state its authoritative identity and whether a present but
   incorrect strong identity is forbidden from falling back to a weaker match.
5. Canonical identity adoption does not trace old-to-new propagation through every persisted,
   mirrored, indexed, emitted, and returned copy, with an end-to-end old-to-new test.
6. Online adoption and offline repair can choose different winners because the plan does not
   define one canonical conflict policy.
7. Migration, repair, or soak completion is inferred from resource existence instead of a
   measurable completion condition and decision owner.
8. The confirmation round checks only prior finding ids. It must re-review the whole revised
   plan, re-open the terminal leaves, and invent at least one new hostile case against each
   material revision.

---

## 3. Document review (`/yonko document <type>`)

Creates or reviews a **PAP, PRD, ADR, or technical design**. Same engine, document
evidence semantics. May inspect repositories and contracts to verify claims. **Must not
change production code**, and must not publish anywhere.

Loop: `draft-or-ingest → review → revise → optional single confirmation round → human approval`

```text
1. scripts/init-session.sh --type document --artifact pap|prd|adr|design \
     --doc-mode create|review --mode …
2. Write the Document Docket (templates/document-review.md), pasting the adapter
   checklist for this artifact type from config/document-adapters.yaml
3. scripts/collect-document-evidence.sh --session … --artifact <type> \
     [--mode create|review] [--draft DRAFT.md] [--source …]… [--recon …] [--repo <abs>]…
4. scripts/classify-scope-risk.sh --session …
5. scripts/sanitise-and-hash-packet.sh --session … --docket …/DOC_DOCKET.md
6. CREATE MODE ONLY: Chair drafts <TYPE>.draft.md from the packet, then re-runs
   collect-document-evidence.sh --mode review --draft … and sanitise-and-hash-packet.sh
   (new hash, new version). The council never reviews a document that does not exist.
7. Seat reviewers per scope band (prompts/document-reviewers.md) - identical packet
   Record `task_call` with model slug after each seat (same as implementation).
   Shanks/Blackbeard Task fail → Chair runs `run-external-seat.sh` immediately (human never runs it).
8. scripts/validate-artifact.sh --kind document-findings --file …
9. Normalise / dedup, verify material claims, adjudicate (evidence > votes)
10. Chair writes <ARTIFACT>.revised.md + the review record
    (decisions, assumptions, open questions, rejected findings, remaining risks)
11. Round bulletin; at most ONE confirmation round
12. Engineering Confidence + scripts/finalize-session.sh --verdict …
13. Human runway: ask for acceptance. On acceptance write <TYPE>.final.md. STOP.
```

Adapter checklists (`config/document-adapters.yaml`) guide attention and define expected
sections per artifact type. They do **not** divide the artifact between reviewers - every
reviewer assesses the whole thing.

| Artifact | Output | Focus |
|---|---|---|
| `pap` | `PAP.final.md` | current-state accuracy, alternatives, boundaries, contracts, failure modes, migration, rollout/rollback, deploy order, readiness |
| `prd` | `PRD.final.md` | problem/user clarity, outcome, scope and non-goals, testable requirements, success measures, over-prescription |
| `adr` | `ADR.final.md` | context, alternatives, trade-offs, rationale, consequences, reversibility, assumptions |
| `design` | `DESIGN.final.md` | correctness, completeness, boundaries, contracts, failure behaviour, security/performance, testing, observability, implementability |

Scope bands and Task budgets match plan review. Risk basis is the same honest heuristic.

---

## Script commands (mechanical)

```bash
ROOT=~/.cursor/skills/the-yonko/scripts

# shared
$ROOT/init-session.sh --mode standard [--type implementation|plan|document] \
  [--artifact pap|prd|adr|design] [--doc-mode create|review] [--linked-session <id>] [--id <slug>]
$ROOT/sanitise-and-hash-packet.sh --session … --docket …/DOCKET.md    # review-type aware
$ROOT/record-event.sh --session … --type <name> --data '{…}'
$ROOT/finalize-session.sh --session … --verdict pass|remand|deadlock|adjourned \
  [--confidence high|medium|low] [--reason …]
$ROOT/aggregate-metrics.sh [--type implementation|plan|document]   # learning only
$ROOT/capture-evaluation.sh --session …                            # or auto on finalize (3.9.0)
$ROOT/evals/aggregate-evaluation.py
$ROOT/evals/promote-case.sh --session … --approved-by … --confirm-hash …
$ROOT/review-quality-ledger.sh --session … --record                # or auto on finalize (projection)
$ROOT/review-quality-ledger.sh --session … --annotate \
  --reached-prod yes|no|unknown --human-missed yes|no|unknown [--notes …]
$ROOT/review-quality-ledger.sh --rollup

# implementation
$ROOT/collect-evidence.sh --session … --repo <abs>   # or --workspace <repo-root>
$ROOT/classify-risk.sh --session … [--force quick|full|trivial|low|medium|high|critical]
$ROOT/validate-artifact.sh --kind findings --file …

# plan
$ROOT/collect-plan-evidence.sh --session … --plan PLAN.draft.md [--source …] [--recon …] [--repo …]
$ROOT/classify-scope-risk.sh --session …
$ROOT/validate-artifact.sh --kind plan-findings --file …

# document
$ROOT/collect-document-evidence.sh --session … --artifact pap --mode review --draft … [--source …] [--repo …]
$ROOT/classify-scope-risk.sh --session …
$ROOT/validate-artifact.sh --kind document-findings --file …
```

Do **not** hand-edit `events.jsonl` or invent `packet_hash`. Re-run the sanitise script
after any Docket or artifact change - it bumps `packet_version` and the hash.

Record these when they happen (missing → metrics show gaps, never invent history):
`reviewers_seated`, `task_call` (with `model` slug after each seat), `findings_merged`,
`verification_completed`, `apply` (implementation), `artifact_revised` (plan/document),
`scoped_verify`, `round_complete`, `verdict`.

`finalize-session.sh` writes observational `evidence/execution.json` (band, force, seatBudget,
subagentCalls, models, duration, completed). It never influences routing or model choice.

**Evaluation + review-quality ledger (learning only, 3.9.0):** after metrics/confidence/outcome, finalize runs shared capture → `SESSION/evaluation/review-measurement.json` (canonical) → council-effectiveness → eval-candidate (mark only) → measurement index upsert → **ledger projection** (`review-quality.json` / `_rollup/review-quality-ledger.jsonl`). Capture does not import the ledger. See `docs/EVALUATION-SYSTEM.md`. Promote cases only via `evals/promote-case.sh` (human + hash + secret scan). Proposals under `improvements/candidates/` are suggest-only. Target: 30-50 real reviews before claiming process lift.

---

## Modes

**Standard** (default): auto-apply only **unanimous accepted** seated defects, after
grounding, deploy-note reclassify, and validation.

**Autopilot**: majority ≥ 3/4 (or ≥ 2/3 if Luffy abroad) **including Blackbeard or Luffy**.
Style → drop. Deadlock breaker once, then Chair evidence pass, before human.
Interrupt-only for flow (`Yonko halt`). "one-shot" / "just let it run" → autopilot.

Autopilot changes the **apply gate only**. It never grants permission to commit, push,
publish, or continue from plan review into implementation.

---

## Human interrupt policy

Exhaust before asking: (1) chat/Docket (2) ticket/docs read (3) code/golden path/tests
(4) safe read-only DB/logs if already available.

Ask only for irreducible **product/policy** choices with real blast radius. Never invent
mass-impact product decisions. Never ask the human to re-vote what code already answers.

---

## Evidence, notes, Pass

- Secrets never in packet (dotenv / secrets env files excluded; script scrubs)
- Deploy-order lockfile/client-CI items → **notes**, never Remand
- Ungrounded findings → drop even if unanimous
- Luffy findings: same path as others; never silent drop; not auto-true
- Pass requires: no remaining high/medium defects, Attack cards present, Done when met/n/a, packet hash recorded
- Implementation only: after apply, scoped verify before rematch (BE `./gradlew test --tests …`, FE targeted `yarn test`)
- Plan / document: no scoped production tests exist; confidence uses evidence completeness, verification status, and handoff-artifact presence instead
- Thrash: same root cause after two applies → Deadlock human
- Soft stop: Pass / Deadlock / Adjourned; round ≥ 5 → restless nudge, continue unless halted
- On Pass: print Human runway (do not commit / push / publish)
- On every final Verdict: print **Engineering Confidence** first, then **outcome axes**
  (`review_outcome` / `evidence_completeness` / `deployment_recommendation` /
  `clean_pass_allowed` / presentation headline), then run `finalize-session.sh`.
  Never collapse “no defects” + incomplete evidence into sole PASS or FAIL
  (`docs/EVIDENCE-GRAPH.md`, `outcome.json`).
- Hard fail: if `evidence_completeness=incomplete` with unresolved
  `operational_side_effects` or `cross_repository_consumers`, do **not** headline
  sole Pass / push-ready / clean. Report **Pass with unresolved evidence** and name
  the categories. Chair `--confidence high` is clamped to the outcome ceiling.
- Return / DTO population changes stage in-repo readers into `=== IMPACT READERS ===`
  in the packet. Seats must review those readers; unresolved readers must name the symbol.

Engineering Confidence is evidence-based, not a model self-rating. It reads packet
completeness, evidence collection, risk review, verification status, deterministic checks
(implementation) or handoff artifact (plan/document), graph completeness when present,
plus Chair-supplied reasons. Cap at medium when review_outcome=pass but
evidence_completeness=incomplete. Chair confidence cannot exceed that ceiling.

Passive metrics (`metrics.json`, `aggregate-metrics.sh`) are **learning only**. They never
feed routing, seating, adjudication, or model ranking.

---

## Hard laws (condensed)

1. No commit / push / MR / publish from Yonko
2. Chair alone edits; seats advise
3. Full-scope identical packet; no same-round peer findings
4. Scripts own evidence, risk, packet hash, validation, events
5. Verification and grounding outrank voting
6. Coverage receipts required (DIFF labels / plan sections / document sections)
7. Round bulletin every round
8. Context pack lint before seating (Done when, artifact present, labels, fences, mode)
9. Human runway before any approval; the human approves, not Chair (Zoro)
10. Plan review never auto-implements; document review never auto-publishes
11. End-of-run Engineering Confidence + SUMMARY/metrics (observational; never auto-tune)

Details and prompts: `prompts/`, `templates/`, `config/`. V1 backup:
`~/.cursor/skills/the-yonko-v1-backup/`.
