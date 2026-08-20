# Yonko: A Deterministic Protocol for AI-Assisted Engineering Review

**Author:** Benjamin Clatworthy  
**Status:** Draft  
**Companion:** [`yonko-executive-summary.md`](yonko-executive-summary.md) (short form)  
**Thesis:** Yonko is a repeated multi-model reconciliation workflow made deterministic and auditable.

---

## Abstract

On difficult architectural and correctness questions, a familiar manual pattern emerged: ask several frontier models the same question, compare their answers, notice they often disagreed, chase the disagreement with follow-ups, verify claims against code and contracts, and reconcile the result. The scarce resource was not another model completion. It was disciplined reconciliation - a human chair between conflicting judgements.

That workflow is useful and costly. Without shared evidence, models answer different imagined problems. Without independence, later answers are contaminated by earlier ones. Without a recorded procedure, the same change reviewed twice is not reproducible. Without governance, nothing fails closed when material issues remain open.

This paper describes **Yonko**, a protocol that formalises that workflow for AI-assisted engineering review. The current reference implementation is built as an IDE agent skill using deterministic scripts and file-backed session state; the protocol itself is not tied to that host. Models perform engineering judgement. Scripts and human-owned policy own classification and procedural legality. Reviewers receive an identical evidence packet and remain independent. Humans retain final authority over commits, publishes, and protocol changes. An Engineering Evidence Index stores completed sessions as organisational records, not chat transcripts.

The contribution is not a new model and not an autonomous agent. It is a design pattern drawn from practice: **protocol governs process; evidence governs decisions.** We describe the origin of the protocol, its scope and limitations, architecture, lifecycle, a worked example, qualitative observations from use, non-goals, and trade-offs relative to other approaches.

---

## 1. Introduction: a workflow that kept repeating

AI-assisted coding is now ordinary. What stood out in day-to-day engineering was not that models could draft code. It was how often hard questions ended in **consulting several models and reconciling by hand**.

### 1.1 Manual pattern

In practice the pattern looked like this:

1. Ask one frontier model for a review or architectural read.
2. Ask another the same question (often with the same paste of context - or, worse, a slightly different paste).
3. Sometimes ask a third.
4. Compare outputs.
5. Discover disagreement: one model flags a guarded-delete hazard; another is silent; a third invents a concern that does not survive contact with the code.
6. Manually reconcile: which claims cite real evidence? which are unsupported? what follow-up is needed?
7. Decide.

That role is a **human chair** between models: same problem domain, conflicting judgements, human synthesis. The point of the exercise:

> The goal was not to find "the smartest model."  
> The goal was to construct the highest-confidence engineering decision available.

Yonko exists because that workflow kept repeating. It is automation and governance around that repeated multi-model review workflow.

```text
Manual today                         Yonko
─────────────                        ─────
Claude                               Packet
   │                                    │
GPT                                  Independent reviewers
   │                                    │
Gemini                               Verification
   │                                    │
Engineer compares                    Workflow legality
   │                                    │
Decision                             Evidence
```

### 1.2 What multiple models buy - and what they do not

In practice:

- Different model families have different strengths and failure modes.
- Disagreement is often more valuable than agreement: it marks where further engineering investigation is required.
- **Consensus is not correctness.** Two models can agree and both be wrong.
- The goal of multiple reviewers is **higher confidence under evidence**, not majority voting.
- Relying on a single model simplifies operation but also concentrates failure modes. Whether that trade-off is acceptable depends on the engineering task.

Multiple sources of judgement only help if they share **the same evidence**, remain **independent** long enough to disagree honestly, and submit to **verification** and **human authority**. Without those, multi-model review is expensive and unreproducible.

### 1.3 How the protocol emerged

The architecture came bottom-up from friction:

```text
Repeated manual comparison across models
        │
        ▼
Need for reproducibility
        │
        ▼
Need for common evidence
        │
        ▼
Need for reviewer independence
        │
        ▼
Need for deterministic workflow
        │
        ▼
Need for governance
        │
        ▼
Yonko
```

Each layer is a consequence of the previous pain. The rest of this paper describes the resulting protocol - established engineering ideas (independent review, checklists, evidence, human gates) made explicit for AI-assisted work.

### 1.4 What this paper is not claiming

Yonko did not invent workflow, governance, independent review, evidence, or verification. Those practices long predate large language models. This paper argues that **AI-assisted engineering needs those practices made explicit**, because prompt-only review does not provide them by default.

---

## 2. Scope

Yonko is designed for engineering review workflows where **correctness and auditability matter more than raw throughput**.

Typical fits: material service changes, migrations, auth and tenancy boundaries, contract changes, plan review before implementation, and other work where a wrong “looks fine” answer is expensive.

It is intentionally **heavier** than a single prompt-based review and intentionally **lighter** than fully autonomous agent systems that plan and rewrite their own execution graphs. It assumes an engineer is willing to assemble evidence and act as Chair. It does not aim to maximise tokens processed per hour.

---

## 3. Non-goals

Yonko is intentionally **not**:

- an autonomous software engineer
- a graph execution runtime or dynamic planner that rewrites its own workflow
- a replacement for CI/CD
- a replacement for human code review
- a replacement for engineering judgement
- bound to one model provider or IDE

It is a **governance protocol** for making AI-assisted reviews more trustworthy, reproducible, and auditable - while leaving commits, merges, and production publishes to humans.

---

## 4. Design principles

### 4.1 Reviewer independence

Each seated reviewer receives the **same** evidence packet and assesses the **complete** artifact. Specialist lenses (contracts, correctness, chaos, company-specific requirements) are attention biases, not partitions. In our experience, partitioning review by file ownership optimises throughput and misses cross-cutting defects.

Independence also means reviewers do not see each other’s findings before adjudication. Parallel review reduces anchoring. Adjudication is evidence-weighted, not a vote.

### 4.2 Deterministic workflow

Properties that must be trusted across sessions - packet integrity, seating floors, verification presence, human approval metadata - prefer **scripts and file-backed state** over prompt instructions. Scripts claim only what they enforce. Chair discipline remains necessary for judgement; it is not pretended to be a distributed orchestrator.

### 4.3 Evidence over conversation

The unit of review is a **packet**. Conversation is how the Chair operates the protocol. Conversation is not the archival form of truth. This maps directly to the origin problem: slightly different pastes into different chats are not the same question.

### 4.4 Information preservation

Optimising representation is allowed. Optimising away diffs, tests, contracts, migrations, or deploy ordering is not. Summarising a diff “to save tokens” recreates the inconsistent-paste problem inside the packet.

### 4.5 Human authority

Yonko never commits, pushes, opens merge requests, or publishes to production systems. Plan review never auto-implements. Document review never auto-publishes. Evidence publication requires explicit human hash confirmation. Continuous improvement suggestions never rewrite protocol files.

### 4.6 Protocol governs process; evidence governs decisions

- **Protocol governs process.** Stages, seating minima, packet pinning, verification requirements, finalisation gates.
- **Evidence governs decisions.** Findings cite packet material. Verified evidence outweighs reviewer majority. Historical records inform later work as advisory context - they do not silently auto-tune seating or apply rules.

The protocol is not about replacing engineers or judgement. It is about making AI-assisted review operable under those constraints.

---

## 5. Architecture

### 5.1 Ownership boundaries

```text
                 AI
                  │
        ┌─────────┴─────────┐
        │                   │
 Engineering         Classification
  Judgement                 │
        │                   │
        └─────────┬─────────┘
                  │
         Routing Policy (Human)
                  │
          Reviewer Selection
                  │
         Independent Council
                  │
           Verification
                  │
        Workflow Legality
                  │
          Human Approval
                  │
          Engineering Evidence
```

**AI** contributes engineering judgement and, optionally, closed-enum advisory tags. **Classification** that affects seating is primarily scripted from evidence. **Routing policy** is human-owned. **Selection** is deterministic. The **council** judges. **Verification** checks material claims. **Workflow legality** answers whether the session is procedurally complete. **Humans** approve, publish, and change process. **Engineering Evidence** accumulates canonical records.

Excluded by design: a planner that invents new workflow graphs at runtime; reviewers that recruit other reviewers; metrics that silently rewrite policy.

### 5.2 End-to-end control flow

```text
Developer / Chair
        │
        ▼
   Evidence Packet
        │
        ▼
 Engineering Council
 (independent seats)
        │
        ▼
    Verification
        │
        ▼
  Workflow Legality
        │
        ▼
   Human Approval
  (when required)
        │
        ▼
 Engineering Evidence Index
 (optional publish)
        │
        ▼
 Continuous Improvement
 (suggest only; optional)
```

### 5.3 Council

| Seat | Role | Ownership |
|------|------|-----------|
| Chair | Parent agent; sole writer; operates scripts; adjudicates; applies fixes or revises plans/docs | Process execution + integration (automated form of the human chair) |
| Contracts seat | Compatibility, requirements, API shapes | Judgement |
| Correctness seat | Correctness, concurrency, side effects | Judgement |
| Chaos seat | Omitted failure cases / adversarial reads | Judgement |
| Company-requirements seat | House rules (adapter-gated; optional) | Judgement (optional) |
| Verifier | Confirms material findings against evidence | Judgement support |

Chair is the parent agent, not a peer Task. Reviewers are Tasks with model families resolved from the live allowlist. That preserves the original motivation: **input from multiple sources**, then a single accountable synthesis path - not a popularity contest.

### 5.4 Packet

The packet is content-addressed for the session: built, secret-scanned, hashed, and pinned. Stale packets invalidate progress that depended on the old hash. Implementation packets include repository diffs; plan and document packets include drafts and reconnaissance. When an approved plan preceded implementation, only the approved plan artifact is handed across - not the planning chat.

### 5.5 Workflow legality

File-backed workflow state and events record whether a session is procedurally complete. Sessions can run in enforce mode: confirmed guard violations fail closed (open material findings, missing verification where required, incomplete seating, packet staleness, missing human approval on plan/document pass paths, and related fences). A shadow mode remains for diagnosis.

Limitation stated honestly: this is a legality ledger plus Chair discipline, not a distributed workflow engine. That fits local, file-backed agent work; it is a limitation for organisations that need a central orchestrator service.

### 5.6 Engineering Evidence Index

Completed sessions may be published into a local Evidence Index checkout. Publication is two-phase: candidate preview, then explicit human hash confirmation. The adapter never git commits or pushes. Records are structured and exclude chat transcripts and secret-bearing files by design.

### 5.7 Later iterations

Later iterations added:

- **Deterministic reviewer routing** - change classes from evidence; human-owned policy maps classes to existing seats; explain prints reasons. AI does not invent seats.
- **Suggest-only continuous improvement** - optional analysis when finding classes repeat above a threshold. Suggestions do not rewrite protocol files.

These layers sit on the legality and packet spine; they do not replace human authority or independent review.

---

## 6. Review lifecycle

Three review types share one engine: **plan**, **implementation**, and **document**. They are not fused into one autonomous pipeline.

```text
Plan review
    │
    │  (human decides to implement)
    ▼
Implementation review
    │
    ▼
Verification + adjudication + apply/revise
    │
    ▼
Workflow legality / finalise
    │
    ▼
Evidence publication (optional, human-gated)
    │
    ▼
Future reuse (query / informed_by)
    │
    ▼
Continuous improvement suggestions (optional)
```

Staging forces human decisions at phase boundaries. Our experience suggests the latency cost is repaid in fewer silent scope expansions - the same reason the original multi-chat workflow eventually needed a definition of “done.”

---

## 7. A worked example

**Change.** A developer retargets a credit note and deletes an emptied placeholder parent. Diff touches service code and a migration.

**Packet.** Chair collects git evidence. Risk signals raise the band. Routing policy seats the contracts, correctness, and chaos reviewers (and standards if the org adapter enables it). Packet is hashed and pinned.

**Council.** Reviewers receive the identical packet in parallel.

- Correctness reports a **high** finding: irreversible note rehome runs before a conditional placeholder delete that can no-op when soft-deleted siblings remain.
- Contracts reports a **medium** API compatibility note on an optional field.
- Chaos reports a speculative race that does not cite packet evidence.

**Verification.** The verifier confirms the high finding against the diff and golden-path helpers. The ungrounded finding is rejected. The API note is held.

**Workflow legality.** Finalisation with verdict `pass` **fails closed**: an open high finding remains. The session is not “green” because two of three reviewers were calm. Evidence governs the decision; the protocol refuses rubber-stamps.

**Fix.** Developer reorders eligibility and delete; adds a sibling-remains test. Rematch with a fresh packet hash.

**Re-review.** Material finding resolved and verified. Legality passes.

**Evidence publication.** Human confirms the candidate hash and publishes locally. Optional later: continuous improvement may flag the pattern if it recurs - as a **suggestion**, not an auto-edit to policy.

This is the automated form of three chat windows: compare, verify, refuse false confidence, fix, re-check.

---

## 8. Practical observations

This section is qualitative. It is not a controlled experiment and should not be read as statistical proof. No quantitative evaluation is claimed.

During development and use of the protocol, we observed:

- **Reviewer disagreement** frequently highlighted areas worth further investigation, even when no single model “won.”
- **Deterministic packets** reduced review variance caused by differing context or omitted files.
- **Separating legality from judgement** simplified workflow reasoning: scripts answer “is the session procedurally complete?”; models answer engineering questions.
- **Identical evidence** produced more consistent finding sets across seats than ad hoc multi-chat pastes of the same change.
- **Workflow legality** prevented sessions from being marked complete despite unresolved material findings - including cases where a majority of reviewers had been quiet.
- **Verification** regularly discarded plausible-sounding findings that could not be grounded in the packet.
- **Human gates** on evidence publication and plan approval matched how production organisations already treat merge and release authority.

What we did *not* observe: that more models always produce better outcomes, or that the protocol removes the need for skilled engineers. The opposite: the protocol concentrates scarce attention on reconciliation and evidence.

---

## 9. Limitations

Yonko has clear boundaries. Stating them is part of the claim.

- **Evaluation setting.** Observed primarily on personal and small-team engineering workflows, not large multi-org fleets.
- **No quantitative evaluation yet.** There are no controlled A/B rates, precision/recall figures, or latency benchmarks in this paper.
- **Evidence discipline.** Preparing a useful packet requires engineering care. Thin packets produce thin reviews.
- **Latency.** A full council session is slower than a single prompt. That cost is intentional for material changes and wrong for many exploratory tasks.
- **Does not prove correctness.** Fail-closed legality and verification raise confidence under evidence; they do not certify that shipping is safe.
- **Not for exploratory coding.** Yonko is a review and governance protocol. It is a poor fit for open-ended exploration, spike work, or “just try something.”
- **Local orchestration.** The reference implementation is a legality ledger plus Chair discipline, not a central workflow service.

Researchers and practitioners should treat these as design boundaries, not temporary bugs.

---

## 10. Knowledge accumulation

Chat history is a poor knowledge base. The Evidence Index stores **completed engineering activities** as structured records with finding patterns and artifact references.

1. During a review, retrieved prior evidence is advisory, never a silent seating change.
2. Across many reviews, repeated patterns become visible; continuous improvement may suggest process attention.
3. Humans decide whether to update standards, routing policy, or training.

This is closer to an incident database or ADR corpus than to a vector memory of chats.

---

## 11. Engineering governance

Prompts are necessary for judgement quality and insufficient for legality. A prompt that says “never approve with open critical findings” is not the same as a finalisation path that refuses pass while open material findings remain.

Workflow should own procedural questions. Workflow should not decide whether a retry loop is correct - that remains reviewer and human territory. Humans retain commits, production publishes, and protocol edits. That boundary is operational accountability, not philosophy.

---

## 12. Comparison with engineering approaches

We compare **approaches**, not products.

### 12.1 Prompt-only review

**Works well:** exploratory critique, low-stakes questions, speed.  
**Trade-off:** drift, weak auditability, easy rubber-stamping.  
**Yonko:** keep prompts for judgement; move packet integrity and finalisation gates into scripts.

### 12.2 Single-model review

**Works well:** operational simplicity; one relationship to learn; fine for narrow tasks.  
**Trade-off:** concentrates failure modes; no structured disagreement.  
**Yonko:** optional multi-seat when confidence matters; not mandatory for every trivial change (routing and risk bands exist partly for that reason).

### 12.3 Independent multi-model review (manual)

**Works well:** diversity of failure modes across model families.  
**Trade-off:** expensive; inconsistent pastes; no shared “done.”  
**Yonko:** automate the chair workflow without pretending majority vote is truth.

### 12.4 Graph orchestration / high-autonomy agents

**Works well:** long tool-using exploratory tasks with clear rewards.  
**Trade-off:** harder to audit; planners expand scope; legality and judgement blur.  
**Yonko:** reject runtime graph rewriting for review governance. Trade-off: less autonomy, more human gating - acceptable for production change.

### 12.5 Conventional human review and CI

**Works well:** ownership, tests, release discipline - still the backbone.  
**Trade-off:** alone, does not structure AI participation when AI volume spikes.  
**Yonko:** complement CI and human review; do not replace them.

Yonko is not universally better. It is heavier than a single prompt. It is lighter than a custom agent platform. It is aimed at engineers who already do multi-model reconciliation by hand and want that work to be reproducible.

---

## 13. Lessons learned

The biggest lesson was that the scarce resource wasn’t another model completion. It was disciplined reconciliation.

1. **Automating the chair mattered most.** Synthesis and gates mattered more than adding seats.
2. **Disagreement is a feature** when evidence is shared; noise when it is not.
3. **Deterministic gates are easier to trust** than paragraphs of self-justification when a session cannot finalise.
4. **Evidence compounds; chat does not.**
5. **Full-packet independence** caught cross-cutting issues that specialised slices missed in our use.
6. **Stabilise legality before adding cleverness.** Routing and continuous improvement landed as thin layers after the spine held.
7. **Suggest process change; do not self-amend.** Letting metrics rewrite review policy recreates prompt drift at the meta level.

---

## 14. Future work

**Present capabilities:** workflow legality; Evidence Index; deterministic reviewer routing; suggest-only continuous improvement.

Further work should follow observed friction and human approval: richer evidence retrieval, repository-specific policy overlays, better efficacy measurement without auto-tuning, and hosting-agnostic ports of the same protocol.

---

## 15. Conclusion

This work was not an attempt to automate software engineering. It was an attempt to automate a **review workflow performed repeatedly by hand**: consult multiple models, confront disagreement, verify evidence, and decide with eyes open - without concentrating failure modes in a single model, and without treating consensus as truth.

**Yonko is that workflow made deterministic and auditable.**

**Protocol governs process. Evidence governs decisions.**

AI can improve engineering judgement, but engineering reliability comes from deterministic protocols, evidence, and human governance.

---

## Appendix A: Glossary

| Term | Meaning |
|------|---------|
| Packet | Hashed evidence bundle shared by all reviewers |
| Chair | Parent agent; sole writer; automated form of the human reconciler |
| Council | Independent reviewer seats (multiple model sources) |
| Workflow legality | Procedural completeness of a session |
| Evidence Index | Local canonical store of completed sessions |
| Routing policy | Human-owned map from change classes to seats |

---

*End of draft.*
