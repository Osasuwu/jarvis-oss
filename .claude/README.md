# `.claude/` — project-scoped Claude Code config for jarvis

**Core Jarvis machinery lives at user-level (`~/.claude/`).** It was moved
out of this directory in EPIC #335 (Pillar 7 Phase 0: Federation) so that
Claude Code has the same SOUL, core skills, hooks, and MCP servers
regardless of which project's CWD it's launched from.

Source of truth for the user-level install lives in
[`.claude-userlevel/`](../.claude-userlevel/) at the repo root. It's applied
to `~/.claude/` by [`scripts/install/installer.py`](../scripts/install/installer.py)
(entry points: `install.ps1` / `install.sh`).

## What stays here

Only jarvis-project-specific Claude Code config:

- [`agents/`](agents/) — project-scoped subagent definitions
  (e.g. `coding.md`).
- `settings.json` — intentionally empty (`{}`); project-local hooks go
  here if jarvis ever needs them. The federation-wide hooks live in
  `~/.claude/settings.json` (installed from `.claude-userlevel/settings.json`).

Everything else (the core skills — `implement`, `delegate`, `verify`,
`status`, `reflect`, `end` (with `--quick`), `research`, `goals`,
`self-improve`, `setup-tasks` — plus SOUL.md and `.mcp.json`) was removed
in M5 (#340). They're still available in every session, just from
`~/.claude/` now. (`autonomous-loop` skill file is retained but SUPERSEDED
2026-05-26 — pre-M44 baseline only, do not invoke for new flows.)

## Where to look next

- **Editing a core skill** → `.claude-userlevel/skills/<name>/SKILL.md`.
  Re-run `install.ps1 -Apply` (or `install.sh --apply`) to propagate.
- **Editing hooks** → `.claude-userlevel/settings.json`, then re-apply.
- **Editing MCP servers** → `.claude-userlevel/.mcp.json`, then re-apply.
- **Editing SOUL** → [`config/SOUL.md`](../config/SOUL.md) is the canonical
  location; the installer copies it to `~/.claude/SOUL.md`.
- **Protected-file rules** →
  [`docs/security/agent-boundaries.md`](../docs/security/agent-boundaries.md).
