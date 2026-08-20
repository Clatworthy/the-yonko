# Document review templates (Yonko V3)

Artifact types: `pap`, `prd`, `adr`, `design`. Checklists live in
`config/document-adapters.yaml`. Two modes: `create` (from source material) and
`review` (existing draft).

---

## Document Docket (Chair writes; every seat receives)

```markdown
# Yonko Document Docket

## Artifact
- Type: <pap|prd|adr|design>
- Mode: <create|review>
- Title: <document title>
- Draft path: <path, or "create mode - Chair drafts first">
- Intended audience: <engineering | product | both | leadership>

## Purpose
<what decision or work this document must enable, in one or two sentences>

## Source material supplied
- <file name> - <what it is>

## Reconnaissance performed
- Repositories opened: <paths>
- Key symbols read: <path#symbol>
- Contracts / events / schemas read: <list, or none>
- Existing documents read: <list, or none>

## Claims that must be verified against code
- <claim in the document> - <where to check>

## Done when (for the document itself)
- [ ] every required section for this artifact type is present or justified absent
- [ ] current-state claims are verified against code or contracts
- [ ] requirements are testable and unambiguous
- [ ] unresolved decisions are listed with owners
- [ ] no production code changed by this session
- Out of scope: <…>

## Explicit constraints
- Chair alone revises the document
- No production code changes
- No automatic publication to Confluence or ticket creation
- <project constraints>

## Adapter checklist (from config/document-adapters.yaml)
- <paste the review_for list for this artifact type>

## Risk (from classify-scope-risk.sh)
- Band: <trivial|low|medium|high|critical>
- Basis: heuristic from stated scope and inspected context (NOT diff-derived)
- Reasons: <list>
- Terms not present in artifact (weak signal only): <list>
- Reviewers must hunt omitted scope themselves
```

---

## Packet

Build via script - never hand-assemble.

Review an existing document:

```bash
scripts/collect-document-evidence.sh --session "$SESSION" --artifact pap \
  --draft PAP.draft.md --source ticket.md --recon RECON.md --repo /abs/path/to/repo
scripts/classify-scope-risk.sh --session "$SESSION"
scripts/sanitise-and-hash-packet.sh --session "$SESSION" --docket DOC_DOCKET.md
```

Create from source material (draft first, then rebuild the packet so the council reviews a
real draft):

```bash
scripts/collect-document-evidence.sh --session "$SESSION" --artifact prd \
  --mode create --source ticket.md --source notes.md
scripts/classify-scope-risk.sh --session "$SESSION"
scripts/sanitise-and-hash-packet.sh --session "$SESSION" --docket DOC_DOCKET.md
# Chair drafts PRD.draft.md from the packet, then:
scripts/collect-document-evidence.sh --session "$SESSION" --artifact prd \
  --mode review --draft "$SESSION/PRD.draft.md" --source ticket.md --source notes.md
scripts/sanitise-and-hash-packet.sh --session "$SESSION" --docket DOC_DOCKET.md
```

The second `sanitise-and-hash-packet.sh` bumps `packet_version` and produces a new hash.
Reviewers cite the new hash. Reviewers never review a document that does not exist.

Packet shape:

```text
=== YONKO DOCKET ===
=== REVIEW TYPE ===
=== REPOSITORIES INSPECTED ===
=== TERMS NOT PRESENT IN ARTIFACT (weak signal only) ===
=== SECTION MAP (line / level / heading) ===
=== <PAP|PRD|ADR|DESIGN> UNDER REVIEW ===
=== SOURCE MATERIAL: <name> ===
=== RECONNAISSANCE NOTES (paths and symbols already inspected) ===
```

---

## Round Bulletin (document review)

```text
Yonko Document Round <n> - <artifact type>: <title>
Risk: <band> (heuristic from stated scope)
Seated: <seats> | Packet: v<version> <hash12>

Findings: <n> (critical <c> / high <h> / medium <m> / low <l>)
Inaccurate claims corrected: <n>
Sections added: <n>
Held for human decision: <n>
Rejected as ungrounded: <n>

Top defects:
- <severity> <title> - <one line impact>

Chair revision: <ARTIFACT>.revised.md written
Next: <confirmation round | human approval>
```

---

## Final artifact

Chair writes `<ARTIFACT>.revised.md` after adjudication, and `<TYPE>.final.md`
(`PAP.final.md` / `PRD.final.md` / `ADR.final.md` / `DESIGN.final.md`) once the human
approves. The final file is the document itself, followed by the decision log below.

```markdown
---

# Review record

- Document session: <session id>
- Artifact type: <pap|prd|adr|design>
- Mode: <create|review>
- Risk band at review: <band> (heuristic from stated scope and inspected context)
- Engineering Confidence in this document: <HIGH|MEDIUM|LOW>

## Decisions made
- <decision> - <rationale>

## Assumptions
- <assumption> - <verified against <path>, or unverified>

## Open questions
- <question> - <owner>

## Findings rejected during review
- <finding title> - <why rejected: ungrounded / style preference / duplicate / not applicable>

## Remaining risks
- <risk> - <mitigation or accepted>
```

---

## Human runway (document review)

```text
Document review complete. No production code changed and nothing was published.

1. Read: <session>/<ARTIFACT>.revised.md
2. Inaccurate claims corrected: <n>
3. Held for your decision: <n>
4. Open questions: <n>
5. Engineering Confidence: <LEVEL> because <reasons>

To accept: tell me and I will write <TYPE>.final.md.
To revise: tell me what to change and I will run one more revision.

I will not publish this anywhere. That is your call.
```
