# Engineering Improvement Suggestions

Generated: {{generated_at}}
Window: last {{window_reviews}} reviews (scanned {{records_scanned}})
Threshold: {{min_occurrences}} occurrences
Protocol mutation: **never** (suggest only - human decides)

---

{{#each suggestions}}
## {{title}}

- Pattern: `{{pattern_key}}` ({{group_by}})
- Occurrences: **{{count}}** in the last {{window}} reviews
- Classification: {{classification}}
- Evidence ids: {{evidence_ids_csv}}
- Example titles: {{example_titles_csv}}

{{body}}

**Human decision required.** Do not ask Yonko to rewrite SKILL, routing policy, or prompts from this file.

---
{{/each}}

{{#unless suggestions}}
_No process-level patterns crossed the threshold in this window._
{{/unless}}

## Below threshold (informational)

{{#each below_threshold}}
- `{{pattern_key}}` ({{group_by}}): {{count}} occurrence(s)
{{/each}}
