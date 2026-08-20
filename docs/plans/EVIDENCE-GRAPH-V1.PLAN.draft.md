> **Status: ARCHIVED (implemented).** Live SoT: [`docs/EVIDENCE-GRAPH.md`](../EVIDENCE-GRAPH.md).
> Kept as historical design notes only. Do not treat as a todo list.

# Evidence Graph v1 - Implementation Plan

## Goal

Add a deterministic Evidence Graph layer between routing and packet hash so
implementation reviews (and plan/document reviews where applicable) receive a
bounded, explainable map of upstream reachability, downstream effects,
contracts, persistence, tests, and unresolved edges - without dumping full
repositories or changing models/providers/seats.

## Ticket / source

- Source: human task "Evidence Graph capability" (this chat)
- Constraint: no model assignments, providers, seat composition, Ollama,
  OpenCode, direct API integrations, or model-policy edits
- Constraint: no automatic commit/push; chair-only writes for production code
  (this change is Yonko skill harness only)

## Current sources of truth (do not duplicate)

| Concern | Owner today |
|---------|-------------|
| Diff collection | `scripts/collect-evidence.sh` → `evidence/DIFF-*`, `repos.json`, `DIFF_MAP.txt` |
| Risk band | `scripts/classify-risk.sh` → `evidence/risk.json` (implementation); `classify-scope-risk.sh` for plan/doc |
| Seating | `scripts/route-reviewers.sh` → `evidence/routing.json` |
| Packet assembly + hash | `scripts/lib/assemble_packet.py` via `sanitise-and-hash-packet.sh` |
| Stale detection | `scripts/workflow/state.py` `evidence_fingerprint()` |
| Evidence Index (post-finalize) | `scripts/evidence-index.py` + `config/evidence-taxonomy/v1/` |
| Finding reachability/impact fields | `contracts/finding.schema.json` (reviewer prose; not a graph) |

**Plug-in locus (implementation):** after `route-reviewers.sh`, before
`sanitise-and-hash-packet.sh`. Graph consumes band from `risk.json`; never
rewrites risk or seating.

## Non-goals (v1)

- Language-server / whole-program static analysis
- Graph database
- LLM as authoritative edge source
- Changing model-policy, routing seats, or risk classifier regexes
- Replacing DIFF bodies with summaries (Information Preservation: additive only)
- Making Evidence Index into a knowledge graph runtime

## Design summary

```text
collect-evidence → classify-risk → classify-change → route-reviewers
  → [NEW] build-evidence-graph
  → [NEW] evaluate-completeness (reads risk.json band + policy)
  → sanitise-and-hash-packet (embed graph + completeness)
  → seating (identical packet)
```

### Artifacts written

| Path | Role |
|------|------|
| `evidence/evidence-graph.json` | Nodes, edges, paths, unresolved edges, metrics |
| `evidence/graph-completeness.json` | Per-category status + seating/verdict gates |
| `evidence/evidence-graph-report.md` | Human-readable explainability report |
| Packet sections | `=== EVIDENCE GRAPH ===`, `=== EVIDENCE COMPLETENESS ===` |

### Schemas

Under `contracts/evidence-graph/`:

- `graph.schema.json` - top-level graph document (`schema_version: "1"`)
- `completeness-report.schema.json` - category statuses + gates
- Shared node/edge/unresolved shapes embedded or split as needed

### Config

Under `config/evidence-graph/`:

- `policy.yaml` - budgets, depth defaults by band (reads band; does not classify)
- `java-spring-adapters.yaml` - annotation / path patterns
- `stopping-rules.yaml` - continue/stop predicates + material boundary overrides
- `completeness-gates.yaml` - per-band `blocksSeating` / `blocksCompleteVerdict`

Reuse existing risk bands: `trivial|low|medium|high|critical` from `risk.json`.

### Scripts / library

Prefer one orchestrator + focused modules (stdlib Python + ripgrep + git),
mirroring `scripts/lib/routing.py` style:

```text
scripts/build-evidence-graph.sh          # CLI entry
scripts/evaluate-evidence-completeness.sh
scripts/lib/evidence_graph/
  __init__.py
  build.py                 # orchestrator
  extract_symbols.py       # diff → changed symbols (Java-first)
  trace_references.py      # bounded callers/callees via rg
  discover_spring.py       # declarative edges
  discover_contracts.py
  discover_persistence.py
  discover_tests.py
  query_cross_repo.py      # Evidence Index query if YONKO_EVIDENCE_REPO set
  completeness.py
  report.py
  ids.py                   # stable node/edge ids, deterministic sort
```

Exact filenames may fold if a module is tiny; keep collectors separable.

### Review-type behaviour

| Type | Graph behaviour |
|------|-----------------|
| implementation | Full build from DIFF + repo paths (required before pin when policy says so) |
| plan | Proposed impact graph from plan text + recon named components (lighter; categories may be unresolved) |
| document | Only when adapter / doc signals system-change; else completeness marks graph `not_applicable` with reason |

Backward compat: sessions without graph files remain readable; pin fingerprint
extension only applies when graph files exist or when review_type=implementation
and policy requires them.

## Changed-symbol extraction (Java-first)

From unified diffs + file reads of changed `.java` / config / Flyway / OpenAPI:

Detect where practical (regex + light parsing, not perfect AST):

- class / method add/remove/modify
- signature vs body-only (compare method headers in hunks)
- annotation lines (`@Transactional`, `@PreAuthorize`, mappings, etc.)
- DTO field / Jackson name changes
- migration SQL file changes
- config key changes in `application*.yml` / `.properties`
- dependency lines in `build.gradle` / lockfile (record as config/dependency nodes)

Emit `changed_symbols[]` with `change_kind` enum and evidence hunk refs.

## Upstream / downstream / Spring

**Upstream (bounded):** ripgrep for method/class name references in repo;
classify REST controllers, `@Scheduled`, listeners, tests that match;
confidence: `static` | `framework` | `likely` | `unresolved`.

**Downstream:** parse method bodies / call sites in changed methods for
service/repo calls, HTTP clients, event publish patterns, cache ops;
follow 1-N hops per band policy; stop with recorded reason.

**Spring adapters:** scan changed + discovered files for patterns in
`java-spring-adapters.yaml` (`@RestController`, mappings, `@EventListener`,
`@Transactional`, `@PreAuthorize`, etc.).

## Contracts, persistence, tests, cross-repo

- Contracts: OpenAPI yaml near changed paths; DTO/event schema files; permission
  strings found near changed auth code
- Persistence: entities, repositories, Flyway under `db/migration`, jOOQ
  generated mentions
- Tests: map `*Test.java` / `*IT.java` by symbol name and package; behaviour
  matrix (happy/invalid/unauth/...) with covered | not_applicable | uncovered | unresolved
- Cross-repo: if Evidence Index available, `query` for consumers; else emit
  unresolved edge (`unknown_consumer`) with `required_for_complete_review`
  from gates policy

## Categories (all must appear)

Every graph completeness report lists all of:

`changed_symbols`, `upstream_entry_points`, `inbound_callers`,
`outbound_dependencies`, `framework_reachability`, `contracts`, `persistence`,
`events_and_messages`, `remote_services`, `security_and_permissions`,
`configuration`, `deployment_and_compatibility`, `tests`,
`cross_repository_consumers`, `operational_side_effects`

Each: `covered` | `not_applicable` | `unresolved` + reason, discovery_method,
confidence, evidence refs, unresolved details.

## Stopping rules (materiality over depth)

Continue on public contract, persistence shape, events, remote calls,
permissions, transaction boundary, deploy compatibility, material side effects,
production entry reachability.

Stop on stdlib, unchanged utility, duplicate evidence, outside available repos
(record unresolved), or budget/depth with reason. Material boundaries override
depth caps.

## Completeness gates

- Read `evidence/risk.json.risk` (or scope-risk for plan/doc) - do not reclassify
- `evaluate-evidence-completeness.sh` writes `graph-completeness.json`
- `blocksSeating`: fail closed before hash when true (exit non-zero);
  Chair may document human waiver in docket + `--waive` only if policy allows
  and waiver is recorded in completeness + observability
- `blocksCompleteVerdict`: does not block seating; Chair/adjudicator must not
  claim complete Pass without addressing; extend finalize / verdict guidance
  (prompt + optional `verdict.json` check) without inventing a second risk model

Critical band: material unresolved consumers / migrations / auth enforcement
paths default to `blocksCompleteVerdict: true` (and seating block where
`completeness-gates.yaml` says so).

## Packet / fingerprint / observability

- `assemble_packet.py`: after DIFF MAP (and after DIFF bodies or after routing -
  prefer after routing sections so DIFF hunks stay contiguous), append
  deterministic JSON/text of graph + completeness. DIFF bodies unchanged.
- Protect new section headers from harmful dedupe if needed
- `evidence_fingerprint()`: include `evidence-graph.json`,
  `graph-completeness.json` when present
- `finalize-session.sh` / execution observability: graph build duration,
  collectors, node/edge counts, unresolved counts, completeness by category,
  seating/verdict blocked flags - no token/cost routing
- External slim packet: include graph + completeness summaries (or paths) so
  CLI seats see unresolved edges; do not drop silently

## Workflow / SKILL / docs

- Update `SKILL.md` / `ARCHITECTURE.md` / `DOCUMENTATION.md` step lists
- Short section in DOCUMENTATION; README pointer only
- Template `templates/evidence-graph-report.md`
- `record-event.sh`: `evidence_graph_built`, `evidence_completeness_evaluated`
- Plan/document collectors: optional lighter graph build hooks

## Testing

New: `scripts/test-evidence-graph-smoke.py` + fixtures under
`scripts/fixtures/evidence-graph/` (synthetic Java/Spring mini-tree + patches):

- Symbol kinds: body-only, signature, annotation, DTO field, migration, config
- Reachability: direct/indirect caller, interface, REST, scheduled, listener,
  dead code → unresolved/unreferenced
- Downstream: repository/table, HTTP, event, permission, transactional
- Completeness: all categories present; N/A; unresolved consumer; missing
  migration evidence; verdict-blocking gap
- Explainability: every edge has evidence; inclusion reason; stop reason;
  deterministic ordering
- Packet: graph section present; DIFF preservation smoke still passes
- Fingerprint stale when graph mutates post-pin
- Regression: existing `test-*-smoke.py` suite still passes

## Implementation order

1. Schemas + config (gates/stopping/adapters) + empty graph skeleton
2. `extract_symbols` + fixture diffs
3. `trace_references` + `discover_spring` (minimal patterns)
4. contracts / persistence / tests collectors (heuristic)
5. cross-repo query stub → unresolved when index absent
6. completeness evaluator
7. shell wrappers + assemble_packet + fingerprint + events
8. SKILL/ARCHITECTURE/DOCUMENTATION + report template
9. Smokes + run full existing smoke suite
10. Yonko implementation review on this skill diff; fix validated findings
11. Stop before commit/push

## Done when

- Acceptance criteria 1-12 from the task brief are met for v1
- No edits under `config/model-policy.yaml` or provider/seat files
- Example graph produced for one fixture Java/Spring change
- Known limitations documented (no LSP; Java-first; cross-repo needs Index)

## Known limitations (accept for v1)

- Java/Spring primary; other languages: file-level + unresolved categories
- Callers via text search, not full type resolution (false positives possible;
  confidence labelled)
- Semantic meaning changes (same String, new enum values) flagged as
  `likely` / unresolved for human/model interpretation, not proven edges
- Reflective registration largely unresolved unless pattern-matched

## Explicit out of scope this plan

Model policy / DeepSeek / Luna seating / BYOK / Ultra / packet token slicing
