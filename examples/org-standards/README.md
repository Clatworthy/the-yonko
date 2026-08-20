# Example: company-specific requirements adapter (optional)

Yonko runs without this. Use it when you want Luffy to enforce **your company's**
engineering requirements (house rules that would not apply at a different company).

## Enable locally (does not pollute the shared skill)

```bash
cp examples/org-standards/project-adapters.yaml \
   ~/.cursor/skills/the-yonko/config/project-adapters.local.yaml
```

Edit the skill paths to point at your standards docs / Cursor skill.
Set `YONKO_PROJECT_ROOT` to your checkout root.

`project-adapters.local.yaml` is gitignored.

## Pattern

```yaml
adapters:
  my-org:
    detect:
      workspace_markers: ["README.md"]
      path_contains: "/my-org"
    luffy:
      enabled: true
      skills:
        - "${YONKO_PROJECT_ROOT}/path/to/engineering-standards/SKILL.md"
```

Or run the generic installer:

```bash
bash ~/.cursor/skills/the-yonko/scripts/setup.sh
```

See `SHARE.md`. Author: Benjamin Clatworthy.
