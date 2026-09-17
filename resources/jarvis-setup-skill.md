---
pairs_with: docs/writing-into-user-owned-files.md
harnesses: all — see docs/harnesses.md
cost: reader's time — one short trial-run read, plus the trial-or-full question asked before anything is written
---

# jarvis-setup skill

The semantic-delta option (7) in
[`writing-into-user-owned-files.md`](../docs/writing-into-user-owned-files.md), plus its
show-before-writing trial mode:
[`.agents/skills/jarvis-setup/SKILL.md`](../.agents/skills/jarvis-setup/SKILL.md).

## How you know it ran

Trial mode prints the delta as a fenced block and writes nothing. Full mode writes via option 4
on include-capable harnesses (an owned file, e.g. `.claude/jarvis.md`, plus one bare `@import`
line — `/context` should list the owned file as loaded) or via option 3 elsewhere (a
`<!-- jarvis-setup:begin -->` … `<!-- jarvis-setup:end -->` block in the rules file) — `git diff`
after running shows exactly what changed. A re-run replaces that file or block whole, and the
skill's own §7 documents how to remove it again.
