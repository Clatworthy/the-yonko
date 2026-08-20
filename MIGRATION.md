# Migration notes

**New install?** Skip this file. Use [`SHARE.md`](SHARE.md) / `scripts/setup.sh`.

This file only matters if you used an older Yonko layout.

## Breaking rename (V3)

| Want | Old | Now |
|------|-----|-----|
| Full council on a diff | `/yonko plan` | `/yonko full` |
| Review a plan before coding | (did not exist) | `/yonko plan` |

`classify-risk.sh --force plan` exits with an error pointing at `--force full`.

## Doc cleanup (V3.6+)

Removed historical stubs from the repo root: `briefs.md`, `AUDIT.md`, `V2.1.md`,
`V3.md`, `IMPLEMENTATION.md`. Runtime truth is `SKILL.md` + `prompts/` + `templates/`
+ `scripts/`. Human guide: `DOCUMENTATION.md`. Patterns: `ENGINEERING-PATTERNS.md`.
Living constraints: `V4.md`.

V1 backup (if you still have it): `~/.cursor/skills/the-yonko-v1-backup/`.

For detailed era-by-era history, use `git log` on this repository.
