---
pairs_with: docs/setup-delta-only.md
harnesses: all — see docs/harnesses.md
cost: reader's time — one short trial-run read, plus a yes/no confirmation before the full write
---

# jarvis-setup skill

The tool behind [`setup-delta-only.md`](../docs/setup-delta-only.md):
[`.agents/skills/jarvis-setup/SKILL.md`](../.agents/skills/jarvis-setup/SKILL.md).

## How you know it ran

Trial mode prints the delta as a fenced block and writes nothing; full mode leaves a new
`## Jarvis` section (or a brand-new rules file) in the repo's rules file — `git diff` on that
file after running shows exactly what changed.
