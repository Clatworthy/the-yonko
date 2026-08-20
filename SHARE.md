# Install and share Yonko

Anyone can clone and use Yonko. No org-specific config is required.

**Five concepts to start:** Chair · Packet · Council · Risk band · Human authority.  
Invariants: [`docs/INVARIANTS.md`](docs/INVARIANTS.md). Runtime pin: `VERSION` (current **3.9.0** includes observational evaluation).

```text
Protocol governs process.
Evidence governs decisions.
```

**Yonko counters vibe coding.** Generate-glance-ship is the default when AI writes most
of the diff. Yonko adds standards and guardrails: shared evidence packet, independent
multi-model review, script harness (hash / schema / legality), bounded rematch loops, and
human authority on commit and publish. AI stays productive. Uncriticised AI does not ship.

## Install

```bash
git clone git@github.com:Clatworthy/the-yonko.git ~/.cursor/skills/the-yonko
bash ~/.cursor/skills/the-yonko/scripts/setup.sh
```

Or clone elsewhere and symlink to `~/.cursor/skills/the-yonko`, then run `scripts/setup.sh`.

Needs: Cursor + Task models for Cursor seats (Chair/Shanks).

**Recommended:** [OpenCode Go](https://opencode.ai/docs/go/) ($10/mo after first month; $12 usage / 5h)
plus hybrid profile - grind reviewers off Cursor API credits:

```bash
scripts/set-execution-profile.sh --profile cursor-opencode-go
# then: /yonko doctor
```

Models: `config/model-selections.json`. Fallback without Go: leave / set `cursor-standard`.

### Cursor run mode (recommended)

Yonko fires many harness scripts. Use Cursor **Run Everything** so those are not stuck on
approval prompts. Put [Destructive Command Guard (dcg)](https://github.com/Dicklesworthstone/destructive_command_guard)
in front of the shell first - it blocks `git reset --hard`, `rm -rf`, and similar before
they run:

```bash
curl -fsSL "https://raw.githubusercontent.com/Dicklesworthstone/destructive_command_guard/main/install.sh?$(date +%s)" | bash -s -- --easy-mode
```

That installer detects Cursor and installs the hook. Then:

**Settings → Agents → Approvals & Execution → Run Mode → Run Everything**, and start a
new agent chat.

Optional, if you prefer Auto-review instead: `scripts/setup.sh --cursor-autorun`
(see [`examples/cursor-autorun/`](examples/cursor-autorun/)).

Out of the box after `setup.sh`:

- Chair (Zoro), Shanks, Blackbeard, Buggy
- Plan / implementation / document review
- Workflow legality and Evidence Index pattern
- A starter `config/project-adapters.local.yaml` (gitignored)
- **Luffy is abroad** until you point the adapter at your company's requirements

Author: Benjamin Clatworthy.

## Optional: Luffy (company-specific requirements)

Shanks, Blackbeard, and Buggy review like independent engineers: contracts,
correctness, and omitted failure cases. Those lenses apply to any codebase.

**Luffy is the house-rules seat.** He reviews the change against **your company's**
engineering requirements: the rules that are true here and would not apply at a
different company. Typical inputs are an internal engineering-standards skill,
architecture rules, ship/done policy, or an internal review bot. Yonko does not
ship anyone else's house rules.

Yonko works without him. He stays abroad until you fill a local adapter.

1. Open `config/project-adapters.local.yaml` (created by `setup.sh`).
2. Set `path_contains` / `workspace_markers` for your checkout.
3. Point `luffy.skills` at your company requirements (skill, rules, or policy docs).
4. Set `YONKO_PROJECT_ROOT` to your checkout root when seating Luffy.

Keep that `.local.yaml` private (gitignored). That is the only company-specific
step. Everything else is the same council.

Chair merge order:

1. `config/project-adapters.yaml` (shipped; Luffy off)
2. `config/project-adapters.local.yaml` if present (gitignored)

If no adapter matches with `luffy.enabled: true`, do not seat Luffy
(`Luffy is abroad.`).

## Before sharing this repo

- Do not commit `config/project-adapters.local.yaml` or any org-private TEAM notes
- No absolute home paths in committed files: `rg '/Users/' .`
- No session artefacts or Evidence Index dumps with real work

## Related

| Doc | Use |
|-----|-----|
| [`DOCUMENTATION.md`](DOCUMENTATION.md) | Full guide |
| [`docs/INVARIANTS.md`](docs/INVARIANTS.md) | Protocol freeze |
| [`docs/EXECUTION-PROFILES.md`](docs/EXECUTION-PROFILES.md) | Runtime profiles |
| [`docs/providers/OPENCODE-GO.md`](docs/providers/OPENCODE-GO.md) | OpenCode setup |
| [`docs/EVIDENCE-GRAPH.md`](docs/EVIDENCE-GRAPH.md) | Impact graph |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Mechanical design |
| `examples/org-standards/` | Luffy adapter template (company-specific requirements) |
