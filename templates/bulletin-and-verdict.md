# Bulletin + Verdict templates (Yonko V2/V3)

For **implementation review**. Plan review bulletins are in `templates/plan-review.md`;
document review bulletins are in `templates/document-review.md`. The Final Verdict and
Engineering Confidence blocks below apply to all three review types.

## Round Bulletin (Chair → human, every round)

Post in chat before rematch or final Verdict. Keep short.

```markdown
### Round N bulletin

**Mode:** standard | autopilot
**Risk:** <band> — <top reasons>

**Coverage**
- Shanks/Blackbeard/Buggy/Luffy: ok | rematch | not seated

**Done when**
- [ ] item - met | n/a | unmet

**Must fix this round** (accepted after adjudication)
- Y1 (high): <one line> — evidence: …

**Held** (needs verify / breaker / human)
- Y2: …

**Dropped**
- ungrounded / style / duplicate: …

**Deploy order (noted, not failing)**
- none | model → CI client tag → lockfile → consumer merge

**Chair did**
- Y1: <what landed> — why: … — files: …

**Verify**
- cmd: `…` — green | red | skipped

**Next**
- Rematch | Pass | Deadlock breaker | Deadlock - need you | Thrash - need you
```

Also append substance to session `bulletins.md` (Chair may append; prefer `record-event.sh` for machine events).

## Final Verdict

Print **Engineering Confidence first**, then **outcome axes**, then legacy protocol verdict.
See `templates/end-of-run.md`. Never collapse "no defects" into sole PASS when evidence is incomplete.
If `clean_pass_allowed` is false, headline **Pass with unresolved evidence** and name the
incomplete categories - never push-ready / clean / sole Pass.

```markdown
### Engineering Confidence

**HIGH** | **MEDIUM** | **LOW**

because:
- ✓|✗|? packet complete
- ✓|✗|? evidence collected for <review type>
- ✓|✗|? risk reviewed (<diff-derived | heuristic from stated scope and inspected context>)
- ✓|✗|? verification complete (or n/a)
- ✓|-|? deterministic checks passed / recorded   (- = n/a for plan and document review)
- ✓|✗|- handoff artifact written                 (- = n/a for implementation review)
- ✓|✗|? evidence graph completeness complete
- ✓|✗|? deployment straightforward
- Chair one-liner: …

### Outcome axes (required)

- **Headline:** from `presentation.headline` (not bare Pass when incomplete)
- **Clean pass allowed:** true | false
- **Review outcome:** pass | findings | inconclusive
  - Label example: No validated defects found
- **Evidence completeness:** complete | incomplete
  - Label example: Incomplete - external consumers unresolved
- **Deployment recommendation:** proceed | proceed_with_caveat | block

### Legacy protocol verdict

**Verdict:** Pass | Remand | Deadlock | Adjourned
**Review type:** implementation | plan | document (<artifact type>)
**Risk route:** … (<basis>)
**Packet:** vN / <hash prefix>
**Subagent calls this session:** N / budget M

**Human runway**
1. Full checks: `<exact cmd(s)>`
2. Commit / push: only when you say so
3. Multi-repo MR order: <… | n/a>
4. Deploy-order notes still open: <none | bullets>
5. Evidence gaps still open: <none | bullets from outcome.json>
```

Then finalize (observational - does not change council behaviour):

```bash
scripts/finalize-session.sh --session "$SESSION" --verdict pass \
  --confidence high \
  --reason "deployment straightforward" \
  --chair-note "…"
# writes SUMMARY.md, metrics.json, confidence.json
```

Machine form: write `verdict.json` and validate:

```bash
scripts/validate-artifact.sh --kind verdict --file "$SESSION/verdict.json"
```

## Ceremony (keep light)

- `The Yonko take their seats.` + roster
- `Log Pose locked.` + short Docket summary
- `Yonko report.`
- Round bulletin
- `The Chair charts the fix.` when applies land
- Round ≥ 5: `The seas grow restless.` (budget nudge; continue unless human stops)
- Final Verdict line + Engineering Confidence + SUMMARY.md
