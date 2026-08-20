# Yonko: A Deterministic Protocol for AI-Assisted Engineering Review

**Author:** Benjamin Clatworthy  
**Form:** Executive summary (3-4 pages)  
**Full paper:** [`yonko-deterministic-engineering-governance.md`](yonko-deterministic-engineering-governance.md)  
**Status:** Draft  

**Thesis:** Yonko is a repeated multi-model reconciliation workflow made deterministic and auditable.

> The goal is not the smartest model. The goal is the highest-confidence engineering decision available.

---

## Problem

On hard engineering questions, a manual pattern kept repeating: ask several frontier models the same question, compare answers, watch them disagree, verify claims against code, and reconcile the result by hand.

The scarce resource was not another completion. It was **disciplined reconciliation** - a human chair between conflicting judgements. The goal was not the smartest model. It was the highest-confidence engineering decision available from multiple sources of judgement.

That workflow fails in predictable ways: inconsistent pastes, no shared “done,” rubber-stamps when most models are quiet, and no durable organisational record.

---

## Scope

Yonko is designed for engineering review workflows where **correctness and auditability matter more than raw throughput**.

It is intentionally heavier than prompt-based review and intentionally lighter than fully autonomous agent systems.

---

## One picture

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

---

## Architecture (compressed)

| Concern | Owner |
|---------|--------|
| Engineering judgement | Independent model-backed reviewers on one packet |
| Classification / seating | Scripts + human-owned routing policy |
| Legality (“is this session done?”) | File-backed workflow gates |
| Commits, publish, protocol edits | Humans |
| Institutional memory | Engineering Evidence Index (chat is not the archive) |

**Reference implementation:** IDE agent skill with deterministic scripts and file-backed state. The protocol is not tied to that host.

**Non-goals:** not an autonomous engineer; not a graph runtime; not a CI/CD replacement; not a replacement for human review or judgement.

**Spine:** Protocol governs process. Evidence governs decisions.

---

## Practical observations (qualitative)

During use we observed:

- disagreement often marked where further investigation was required
- deterministic packets reduced variance from differing context
- separating legality from judgement simplified workflow reasoning
- identical evidence produced more consistent findings across seats
- legality gates blocked “pass” while material findings remained open
- verification discarded plausible but ungrounded claims

We did not observe that more models are always better. Relying on a single model simplifies operation but concentrates failure modes; whether that trade-off is acceptable depends on the task.

---

## Limitations

- Evaluated primarily on personal and small-team workflows
- No quantitative evaluation yet
- Requires discipline to prepare evidence packets
- Adds latency versus a single prompt
- Does not prove correctness
- Not intended for exploratory coding

---

## Trade-offs

| Approach | Strength | Cost |
|----------|----------|------|
| Prompt-only | Fast | Drift, weak audit |
| Single model | Simple | Correlated misses |
| Manual multi-model | Diverse judgement | Expensive, unreproducible |
| High-autonomy graphs | Long trajectories | Hard to audit |
| Yonko | Reproducible chair workflow | Heavier than a prompt |

Yonko complements human review and CI. It does not replace them.

---

## Lessons

The biggest lesson: automating reconciliation and gates mattered more than adding seats.

Disagreement under shared evidence is useful. Consensus is not correctness. Suggest process improvements; do not let the system rewrite its own constitution from metrics.

---

## Conclusion

This was not an attempt to automate software engineering. It was an attempt to automate a review workflow performed repeatedly by hand.

**Yonko is that workflow made deterministic and auditable.**

AI can improve engineering judgement, but engineering reliability comes from deterministic protocols, evidence, and human governance.

---

*For architecture detail, worked example, and full comparisons, see the companion paper.*
