# Consult the Yonko

> An evidence-driven engineering council that plans, reviews, documents and preserves institutional engineering knowledge.

![Yonko Council](assets/yonko-council.jpg)

*Plan it. Prove it. Preserve it.*

## What you need to know (five concepts)

| Concept | Meaning |
|---------|---------|
| **Chair** | Parent agent; sole writer |
| **Packet** | One shared hashed evidence bundle (the Docket is the human brief inside it) |
| **Council** | Independent reviewers on that packet (Cursor Tasks and/or OpenCode Go) |
| **Risk band** | How heavy the review is (scripts size the council) |
| **Outcome axes** | Defects, evidence completeness, and deploy advice stay separate (`outcome.json`) |
| **Human authority** | You approve, commit, push, and publish - not the agent |

**Spine:** Protocol governs process. Evidence governs decisions.  
**Invariants:** [`docs/INVARIANTS.md`](docs/INVARIANTS.md)

Yonko counters vibe coding: AI may draft and review; scripts and humans own process and ship.

## Architecture (how to talk about it)

| Layer | What it is |
|-------|------------|
| **Core protocol** | Packet, Evidence Graph, council, routing, verification, legality, human authority |
| **Execution** | Profiles (`cursor-standard` / `cursor-opencode-go` / `cursor-max`) + runtime adapters |
| **Optional operational** | Evidence Index, continuous improvement (suggest-only), efficiency reporting, **evaluation / council effectiveness (3.9.0)** |
| **Reference implementation** | This Cursor skill (`SKILL.md` + `scripts/` + `config/`) |

## Install

```bash
git clone git@github.com:Clatworthy/the-yonko.git ~/.cursor/skills/the-yonko
bash ~/.cursor/skills/the-yonko/scripts/setup.sh
# recommended: OpenCode Go + hybrid profile
#   https://opencode.ai/docs/go/
#   scripts/set-execution-profile.sh --profile cursor-opencode-go
#   /yonko doctor
# optional: edit config/project-adapters.local.yaml for Luffy (company-specific requirements)
```

**Recommended runtime:** `cursor-opencode-go` - Cursor for Chair/Shanks, [OpenCode Go](https://opencode.ai/docs/go/) for Blackbeard/Buggy/Luffy. Best cost for frequent council runs; see [`docs/EXECUTION-PROFILES.md`](docs/EXECUTION-PROFILES.md).

Details: [`SHARE.md`](SHARE.md). Recommended Cursor setup: **Run Everything** plus [Destructive Command Guard](https://github.com/Dicklesworthstone/destructive_command_guard). Optional Auto-review hints: [`examples/cursor-autorun/`](examples/cursor-autorun/).

## Invoke

| Command | Purpose |
|---------|---------|
| `/yonko` / `/yonko review` | Implementation review (diff) |
| `/yonko full` | Force high / full council |
| `/yonko quick` | Lighter route (safety floor still applies) |
| `/yonko plan` | Plan review → `PLAN.approved.md` after you approve |
| `/yonko document pap\|prd\|adr\|design` | Document review → `<TYPE>.final.md` after you approve |
| `/yonko explain` | Why these seats / legality |
| `/yonko doctor` | Validate execution profile setup (no secrets) |
| `/yonko evidence publish` | Optional: local Evidence Index (hash-confirmed) |
| `/yonko improve` | Optional: suggest-only continuous improvement |

Natural language works: `Consult the Yonko`, `review this plan with the Yonko`.

## Council seats

| Seat | Role |
|------|------|
| **Chair (Zoro)** | Parent agent; only writer |
| **Shanks** | Contracts / compatibility |
| **Blackbeard** | Correctness / concurrency |
| **Buggy** | Chaos / omitted cases |
| **Luffy** | Company-specific requirements (optional; you plug in house rules) |

**Recommended:** `cursor-opencode-go` - Chair + Shanks on Cursor; Blackbeard / Buggy / Luffy on [OpenCode Go](https://opencode.ai/docs/go/) (`config/model-selections.json`). `scripts/set-execution-profile.sh --profile cursor-opencode-go` then `/yonko doctor`.

## Docs map

| Doc | Audience |
|-----|----------|
| This README + [`SHARE.md`](SHARE.md) | Newcomers |
| [`docs/INVARIANTS.md`](docs/INVARIANTS.md) | Freeze / proposal gate |
| [`docs/EXECUTION-PROFILES.md`](docs/EXECUTION-PROFILES.md) | Runtime profiles (Cursor / OpenCode) |
| [`docs/providers/OPENCODE-GO.md`](docs/providers/OPENCODE-GO.md) | OpenCode Go setup |
| [`SKILL.md`](SKILL.md) | Agent runtime truth |
| [`DOCUMENTATION.md`](DOCUMENTATION.md) | Full guide (deep; §§11.5 / 24-26 EG / profiles / separation / three-axis) |
| [`docs/EVIDENCE-GRAPH.md`](docs/EVIDENCE-GRAPH.md) | Evidence Graph + cross-repo Index + three-axis |
| [`docs/EVIDENCE-EXECUTION-SEPARATION.md`](docs/EVIDENCE-EXECUTION-SEPARATION.md) | Packet vs runtime contract |
| [`CHANGELOG.md`](CHANGELOG.md) | Version deltas (`VERSION` pin; current **3.9.0**) |
| [`docs/EVALUATION-SYSTEM.md`](docs/EVALUATION-SYSTEM.md) | Evaluation / council effectiveness (observational) |
| [`docs/EVAL-CORPUS.md`](docs/EVAL-CORPUS.md) | Human-gated eval corpus + replay |
| [`docs/PROMPT-PREFIX-STABILITY.md`](docs/PROMPT-PREFIX-STABILITY.md) | Cache-friendly prompt ordering |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Mechanical design |
| [`ENGINEERING-PATTERNS.md`](ENGINEERING-PATTERNS.md) | Harness / loop diagrams |
| [`V4.md`](V4.md) | Observe → measure → understand → optimise (maintainers) |
| [`papers/`](papers/) | Protocol paper |

Author: **Benjamin Clatworthy** ([Clatworthy/the-yonko](https://github.com/Clatworthy/the-yonko)).

Sessions: `~/.cursor/yonko-sessions/`. Evidence Index (optional): `YONKO_EVIDENCE_REPO`.

