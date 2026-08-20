# Engineering patterns in Yonko

> How Yonko uses **harness**, **protocol-graph**, and **loop** engineering -
> and what it deliberately refuses.

Yonko is not "more prompts." It is a **counter to vibe coding**: standards and
guardrails stacked so models can judge while scripts and humans still own process.

```text
Protocol governs process.
Evidence governs decisions.
```

---

## Map: which discipline owns what

| Discipline | What we build | Owner | Not this |
|------------|---------------|-------|----------|
| **Harness engineering** | Scripts, contracts, hashes, exit codes around model judgement | `scripts/` + `contracts/` | Trusting the model to self-enforce legality |
| **Protocol-graph engineering** | A **fixed** control-flow graph of stages (init → packet → seat → review → verify → finalize) | `SKILL.md` + workflow scripts | A runtime that invents new graphs mid-session |
| **Loop engineering** | Bounded rematch / confirmation loops with budgets | `review-types.yaml` + risk budgets | Unbounded "keep going until it feels done" |
| **Packet engineering** | One hashed evidence packet for every seat | collect + sanitise scripts | Different pastes per model |
| **Routing / policy engineering** | Deterministic seat selection from risk + change classes | `routing-policy.yaml` | AI inventing seats |
| **Governance / legality** | Fail-closed session completeness | `workflow/` + `workflow-policy.yaml` | Prompt-only "please don't rubber-stamp" |
| **Knowledge loop** | Evidence Index + suggest-only continuous improvement | evidence + CI scripts | Auto-rewriting the constitution from metrics |

---

## 1. Harness engineering

Models discover. The harness refuses to pretend they also enforce.

```mermaid
flowchart TB
  subgraph Harness["Harness (scripts + contracts)"]
    A[init-session] --> B[collect evidence]
    B --> C[classify risk / change]
    C --> D[route reviewers]
    D --> EG[build evidence graph]
    EG --> E[sanitise + hash packet]
    E --> INV[invoke-seat via frozen profile]
    INV --> F[validate findings / verdict schemas]
    F --> G[workflow legality / finalize]
  end

  subgraph Judgement["Judgement (models + Chair)"]
    R[Independent council - Cursor and/or OpenCode]
    V[Verifier]
    Z[Chair adjudicates + applies]
  end

  INV --> R
  R --> F
  F --> V
  V --> Z
  Z --> F
  Z --> G
```

**What the harness guarantees**

- Packet hash / pin freshness
- Evidence Graph completeness (or explicit human waiver) before seating
- Finding and verdict shape
- Seating floors and verifier requirements (when legality enforce mode is on)
- Frozen execution profile + model-selection resolution (fail closed)
- Secret scrub and path fences
- Finalize only when procedural gates pass

**What the harness does not guarantee**

- That a finding is *right*
- That the Chair's fix is *complete*
- That shipping is *safe*

Those remain engineering judgement + human authority.

Evidence Graph (impact map, not the protocol graph): nodes = changed symbols +
callers/callees/tests/consumers; edges = `called_by` / `calls` / `tested_by` / `consumes`.
See [`docs/EVIDENCE-GRAPH.md`](docs/EVIDENCE-GRAPH.md).

---

## 2. Protocol-graph engineering (fixed graph, not a graph runtime)

Yonko **does** graph engineering: stages, edges, and fail-closed gates are designed as a
control-flow graph.

Yonko **does not** run a graph *engine* that rewrites that graph at runtime.

```mermaid
flowchart LR
  subgraph Fixed["Fixed protocol graph (human-designed)"]
    I[Invoke] --> S[Session]
    S --> P[Packet]
    P --> T[Route + seat]
    T --> C[Council]
    C --> V[Verify]
    V --> A[Adjudicate]
    A --> L{Legality}
    L -->|fail closed| X[Stop / rematch]
    L -->|pass| H[Human runway]
  end

  subgraph Forbidden["Forbidden: runtime graph rewrite"]
    PL[Planner invents new stages]
    PL --> PL2[Reviewers recruit reviewers]
    PL2 --> PL3[Metrics auto-edit policy]
  end
```

| | Fixed protocol graph (Yonko) | Dynamic agent graph (refused) |
|--|------------------------------|-------------------------------|
| Who designs stages | Humans in skill + scripts | Model mid-flight |
| Can edges change mid-session? | No (except declared rematch budget) | Yes |
| Auditability | High - same path every time | Low - trajectory drifts |
| Fit for production review | Strong | Weak |

The Chair **walks** the graph. The Chair does not **redraw** it.

---

## 3. Loop engineering

Three nested loops, each with a hard stop.

### Outer: developer lifecycle loop

Humans choose each phase. No fused autopilot from ticket to merge.

```mermaid
flowchart TB
  T[Ticket / intent] --> PL["/yonko plan"]
  PL --> PA[PLAN.approved.md]
  PA --> IMP[Implement outside Yonko]
  IMP --> RV["/yonko review"]
  RV --> HR[Human runway]
  HR --> GIT[Human commit / push]
  GIT --> EV["/yonko evidence publish"]
  EV --> CI["/yonko improve optional"]
  DOC["/yonko document"] -.-> PA
```

### Middle: implementation rematch loop (bounded)

Declared in `config/review-types.yaml`:

```text
review → adjudicate → apply → verify → bounded re-review
```

Budgets scale with risk band (example from policy): trivial 2 … critical 7 rounds.

```mermaid
flowchart TB
  R[Seat council on packet] --> F[Findings + validate]
  F --> VER[Verify material findings]
  VER --> ADJ[Chair adjudicates]
  ADJ --> Q{Open material?}
  Q -->|no| PASS[Verdict + finalize]
  Q -->|yes| APP[Chair applies fix]
  APP --> SCOPED[Scoped verify]
  SCOPED --> BUDGET{Rematch budget left?}
  BUDGET -->|yes| REHASH[Fresh packet hash]
  REHASH --> R
  BUDGET -->|no| DEAD[Deadlock / Adjourned]
```

### Inner: plan / document confirmation loop

At most **one** confirmation round after revise, then human approval. No silent
implementation or publication.

```mermaid
flowchart LR
  REV[Council review] --> FIX[Chair revises artifact]
  FIX --> CONF{Confirmation round?}
  CONF -->|optional once| REV2[Re-review]
  REV2 --> RUN[Human runway]
  CONF -->|skip| RUN
  RUN --> HUM{Human approves?}
  HUM -->|yes| ART[PLAN.approved / TYPE.final]
  HUM -->|no| STOP[Stop]
```

---

## 4. Packet + independence (the fan-out that makes disagreement useful)

```mermaid
flowchart TB
  PK[(Hashed packet.md)]
  PK --> S[Shanks]
  PK --> B[Blackbeard]
  PK --> G[Buggy]
  PK --> L[Luffy if seated]
  S --> ADJ[Adjudication]
  B --> ADJ
  G --> ADJ
  L --> ADJ
  ADJ --> VER[Verifier - material claims only]
```

Same packet. Parallel seats. No cross-reading before adjudication. Evidence outweighs votes.

Seating count is **dynamic** (risk band + change classes). Force full council with
`/yonko full` when you want every lens.

---

## 5. Routing as policy engineering

```mermaid
flowchart LR
  DIFF[Diff / scope] --> RISK[classify-risk / scope-risk]
  DIFF --> CLS[classify-change]
  RISK --> RT[route-reviewers]
  CLS --> RT
  POL[(routing-policy.yaml)] --> RT
  RT --> SEATS[Selected seats + reasons]
  SEATS --> EX["/yonko explain"]
```

AI may add advisory change classes from a **closed enum**. AI never invents seat names.

---

## 6. Knowledge loop (observe → measure → understand → optimise)

Outer institutional loop. Suggest only. Humans still edit protocol.

```mermaid
flowchart TB
  FIN[Finalize session] --> CAND[Evidence candidate]
  CAND --> PUB[Human hash-confirmed publish-local]
  PUB --> IDX[(Evidence Index)]
  IDX --> Q[Future query / informed_by]
  IDX --> IMP["/yonko improve"]
  IMP --> SUG[Engineering Improvement Suggestions]
  SUG --> HUM[Human edits policy / prompts / standards]
```

**Hard rule:** metrics and suggestions never silently rewrite seating, packets, or apply rules.

---

## 7. Contrast: three engineering cultures

```mermaid
flowchart TB
  subgraph Vibe["Vibe coding"]
    V1[Prompt] --> V2[Glance] --> V3[Ship]
  end

  subgraph Auto["High-autonomy graph agents"]
    A1[Planner] --> A2[Rewrite graph]
    A2 --> A3[Long trajectory]
    A3 --> A4[Hard to audit]
  end

  subgraph YonkoStack["Yonko"]
    Y1[Fixed protocol graph] --> Y2[Harness gates]
    Y2 --> Y3[Bounded loops]
    Y3 --> Y4[Human authority]
  end
```

| | Vibe | Dynamic graph agents | Yonko |
|--|------|----------------------|-------|
| Loop | Accidental | Often unbounded | Declared + budgeted |
| Graph | None | Runtime-mutable | Fixed + walked |
| Harness | Weak | Mixed | Script-first gates |
| Goal | Speed | Autonomy | Confidence under evidence |

---

## 8. Where to look in the repo

| Pattern | Primary files |
|---------|----------------|
| Harness | `scripts/*.sh`, `scripts/workflow/`, `scripts/lib/runtime/`, `contracts/` |
| Protocol graph | `SKILL.md`, `ARCHITECTURE.md`, `config/workflow-policy.yaml` |
| Loop shapes | `config/review-types.yaml` (`loop`, budgets, confirmation rounds) |
| Routing | `config/routing-policy.yaml`, `scripts/lib/routing.py` |
| Packet | `sanitise-and-hash-packet.sh`, `templates/docket-and-packet.md` |
| Knowledge loop | `scripts/evidence-index.py`, `scripts/continuous-improvement.py` |
| Philosophy | `V4.md` (observe → measure → understand → optimise) |

---

## One-liner

**Harness wraps judgement. A fixed protocol graph sequences work. Bounded loops rematch until evidence or budget ends. Humans own commits and constitution.**
