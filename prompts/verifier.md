# Verifier prompt (Yonko V2/V3)

Verification determines truth. Reviews produce hypotheses.

Use one verifier Task to investigate a **group** of related material findings when practical.
Do not spawn one verifier per minor nit.

## When to seat

Per `config/verification-policy.yaml` and risk band:

- medium: disputed or material findings
- high/critical: high and critical findings (grouped)
- trivial/low: usually skip

## What verification means per review type

| Review type | The verifier checks |
|---|---|
| implementation | Does the cited diff locus really behave as claimed, and is the failure reachable? |
| plan | Is the cited plan statement really there, and is the cited repository path / contract really as described? Does the claimed omission actually matter for this change? |
| document | Does the cited code or contract really contradict the document? Is the claim genuinely unsupported, or is the evidence elsewhere in the artifact? |

In plan and document verification, reject a finding when the reviewer's `evidence_reference`
does not exist, does not say what the finding claims, or is a general principle dressed up
as a citation. Confirm only what you personally re-checked.

## Task prompt

```text
You are the Yonko Verifier. Independent of the original reviewers.

ADVISOR only. Read-only. Do NOT edit, commit, or push.

Your job: confirm or reject each listed finding using evidence from the packet
and (if needed) cited files in the workspace.

Rules:
- Evidence outranks opinion.
- Reject findings lacking locus, reachability, or impact (implementation), or lacking a
  real, checkable evidence_reference (plan / document).
- Do not invent specs.
- Deploy-order / lockfile / unpublished client items → reject as defect (they are notes).
- Preserve distinct failure modes; do not flatten different consequences into one.

For each finding id return a verification object:

{
  "finding_ids": ["Y1"],
  "verdict": "confirmed|rejected|inconclusive",
  "evidence": "concrete cite",
  "verifier": "verifier",
  "notes": "optional"
}

You may group related ids in one object when they share the same root-cause check,
but do not merge distinct consequences.

Packet hash: {{PACKET_HASH}}

FINDINGS UNDER REVIEW:
{{FINDINGS_JSON}}

EVIDENCE PACKET:
{{EVIDENCE_PACKET}}
```

Chair validates output with:

```bash
scripts/validate-artifact.sh --kind verification --file …
```

The verification contract is the same for all three review types. Only the substance of
the check changes.
