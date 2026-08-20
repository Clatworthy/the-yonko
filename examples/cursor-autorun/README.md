# Cursor Auto-review setup for Yonko (optional)

**Recommended instead:** Cursor **Run Everything** plus
[Destructive Command Guard (dcg)](https://github.com/Dicklesworthstone/destructive_command_guard).
See [`SHARE.md`](../../SHARE.md).

This example is only if you want Auto-review. Yonko harness scripts live under
`~/.cursor/skills/the-yonko` and write sessions to `~/.cursor/yonko-sessions`, which
sits outside a normal project workspace, so Auto-review often asks you to approve
every script.

It steers Auto-review toward allowing the harness (and session writes) while still
asking for push, secrets, and destructive deletes.

## Install

```bash
bash ~/.cursor/skills/the-yonko/scripts/install-cursor-autorun.sh
```

Or manually copy after substituting your home path into `sandbox.json`:

1. Copy `permissions.json` → `~/.cursor/permissions.json` (merge if you already have one)
2. Copy `sandbox.json` → `~/.cursor/sandbox.json` (merge paths if you already have one)
3. Cursor: **Settings → Agents → Approvals & Execution → Run Mode → Auto-review**
4. Start a **new** agent chat (or restart Cursor)

`scripts/setup.sh --cursor-autorun` also runs the installer.

## What each file does

| File | Effect |
|------|--------|
| `permissions.json` | `autoRun` hints: allow Yonko scripts / session writes; still block push and secrets |
| `sandbox.json` | Lets sandboxed commands read the skill and write Yonko session/cache dirs |

## Safety

- This path is Auto-review only. Prefer **Run Everything** plus dcg (SHARE.md) unless you want more prompts.
- Team admin Run Mode policies can override these files.
- Allowlists / classifier guidance are convenience, not a security boundary.
