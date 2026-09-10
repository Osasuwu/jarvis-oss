# Jarvis Architecture

Version: 5.0
Date: 2026-09-10
Status: Active

## 1. System Overview

Jarvis is a personal AI agent built on top of **Claude Code** — not a custom Python application. Claude Code is the runtime; Jarvis adds identity, memory, and skills on top of it.

Since EPIC #335 (2026-04-23), Jarvis is **federated** to user level: the SOUL, the core skills, and the hooks live at `~/.claude/` and load regardless of which project Claude Code was launched in. Since #1800 (2026-09-08), there is no installer and no `.claude-userlevel/` mirroring step — user-level files are edited/registered directly, one device at a time. Project repos only carry project-specific additions.

```
┌──────────────────────────────────────────────────────┐
│                     Claude Code                       │
│                                                       │
│  ~/.claude/SOUL.md       ← Jarvis identity            │
│  ~/.claude/skills/       ← universal slash commands   │
│  ~/.claude/settings.json ← hooks (SessionStart, ...)  │
│  ~/.claude.json          ← MCP registrations (user)   │
│                                                       │
│  <project>/CLAUDE.md     ← project rules              │
│  <project>/.claude/      ← project-specific skills    │
│                            + agents (e.g. coding.md)  │
│                                                       │
│  MCP Servers (registered per-device by hand via       │
│  `claude mcp add --scope user`):                      │
│  ├── github   ← official MCP                          │
│  └── obsidian ← only where a vault exists              │
└──────────────────────┬───────────────────────────────┘
                       │
              ~/.claude/projects/<project>/memory/
              (file-based, per-machine, no sync)
```

## 2. What lives where

### User-level (universal, one device at a time — no installer)

There is no automated propagation to `~/.claude/`. Skills are kept in sync by hand: edit the source under `.claude-userlevel/skills/`, get it reviewed and merged, then manually copy the changed `SKILL.md` into `~/.claude/skills/` on each device. SOUL and CLAUDE.md are edited the same way. MCP servers are registered per-device with `claude mcp add --scope user` — there is no `.mcp.json` file being deep-merged.

| Component | Source in repo | Kept in sync at | Purpose |
|-----------|----------------|--------------|---------|
| Identity | `config/SOUL.md` | `~/.claude/SOUL.md` (manual copy) | Personality, tone, behavior rules (loaded via a **bare, line-start** `@SOUL.md` import in CLAUDE.md — #1328 introduced it, #1426 made it actually resolve) |
| Universal skills | `.claude-userlevel/skills/*/SKILL.md` | `~/.claude/skills/*/SKILL.md` (manual copy) | Core slash commands: `implement`, `dispatch`, `diagnose`, `file-issue`, `grill`, `improve-codebase-architecture`, `research`, `to-tickets`, `triage`, `weekly-release`, `end` |
| Hooks | — (no repo-side source; edited directly) | `~/.claude/settings.json` | SessionStart, PreCompact, PreToolUse protected-file scan |
| MCP servers | — (no repo-side source file) | `~/.claude.json` `mcpServers` block, via `claude mcp add --scope user` | github, obsidian (device-dependent), etc. |

### Project-level (jarvis repo)

| Component | Location | Purpose |
|-----------|----------|---------|
| Project init | `CLAUDE.md` | Session rules specific to the jarvis project |
| Project skills | `.claude/skills/sprint-report/` | Only skill that isn't universal (redrobot release flow) |
| Project subagents | `.claude/agents/coding.md` | Project-scoped coding agent definition |
| Empty hooks | `.claude/settings.json` (`{}`) | Reserved for jarvis-only hooks if ever needed |
| Tombstone | `.claude/README.md` | Redirects readers to `.claude-userlevel/` |

### External Python (only what Claude Code can't do)

| Component | Location | Purpose |
|-----------|----------|---------|
| Hook scripts | `.claude/hooks/*.py`, `scripts/*.py` | SessionStart context, PreCompact backup, secret scanner, protected-file guard |
| Risk scanner | `src/risk_radar.py` | Deterministic pattern scan, no LLM |

Everything else (Telegram, scheduling, background tasks, and — since #1800 — user-level provisioning) uses Anthropic-native features or manual per-device steps, not a custom installer.

## 3. Memory architecture

Memory is native and file-based, per machine, per project — not a custom service. The
Supabase-backed `mcp-memory` MCP server (semantic search via VoyageAI, keyword fallback) was
retired in [#1801](https://github.com/Osasuwu/jarvis/issues/1801) in favor of this, per
[#1790](https://github.com/Osasuwu/jarvis/issues/1790).

```
~/.claude/projects/<project>/memory/
  MEMORY.md         ← always-loaded index, one line per fact, points at topic files
  decisions.md       ← dated decision journal
  <topic>.md         ← detail files MEMORY.md lines point at
```

There is no memory service and no recall tool — reading a file is the recall. Memory is
per-machine: nothing syncs it across devices.

**Memory types:** `user`, `project`, `decision`, `feedback`, `reference`

## 4. Agent model

Claude Code is the main agent. Subagents are spawned for isolated tasks.

```
Owner
  │ CLI / Telegram Channels
  ▼
Claude Code (Sonnet — default)
  │ orchestration, planning, architecture
  ├── Explore subagent (Haiku) ← recon, file reads, searches
  └── general-purpose subagent (Sonnet) ← implementation
```

### Model routing

| Tier | Use for |
|------|---------|
| Haiku | Triage, reports, searches, simple edits |
| Sonnet | Planning, coding, research, debugging |
| Opus | Manual-only, high-risk architectural decisions |

### Permission model

| Agent | Writes | Tools |
|-------|--------|-------|
| Main (Sonnet) | Yes — full workspace | All |
| Explore (Haiku) | No | Read, Glob, Grep, WebFetch, WebSearch |
| Coding (Sonnet) | Branch + PR only | Read, Edit, Bash, `gh` |

## 5. Skills

Universal skills live at `~/.claude/skills/` (source of truth: `.claude-userlevel/skills/`, kept in sync by hand — no installer) and are invoked as `/skill-name` from any CWD. The routing table in `AGENTS.md` describes when each is used.

| Skill | Purpose |
|-------|---------|
| `/implement` | Deliver a single GitHub issue in this session |
| `/dispatch` | Dispatch issues to a coding subagent |
| `/diagnose` | Investigate a bug/incident without necessarily fixing it |
| `/file-issue` | Open a single well-formed tracking issue |
| `/to-tickets` | Break a plan/PRD into multiple end-to-end tracking issues |
| `/grill` | Cross-context CRITIC pass on a consequential decision before it's ratified |
| `/triage` | Sweep issues/PRs for stale metadata, labels, milestones |
| `/research` | Topic investigation, option comparison, autonomous discovery |
| `/improve-codebase-architecture` | Architecture health check + gap analysis |
| `/weekly-release` | Weekly release-notes digest (gated — never sends under the operator's own identity) |
| `/end` | Session closure |

Project-specific skills stay under `<project>/.claude/skills/`. In this repo the only one is `/sprint-report` (redrobot release flow).

## 6. Mobile access

Telegram via **Claude Code Channels** (official Anthropic plugin) — no custom relay code.

Setup: `claude --channels plugin:telegram@claude-plugins-official`

See `docs/setup.md` §8 for full guide.

## 7. Scheduling

Recurring tasks via **Claude Code `/loop`** or Desktop scheduled tasks — no custom scheduler.

Nightly research runs at 03:00, topics configured in `config/research-topics.yaml`.

## 8. Safety baseline

- Coder subagent: branch + PR only, never direct push to `main`
- Human review required before merge
- Protected-file list — canonical in `docs/security/agent-boundaries.md`; enforced at runtime by `.claude/hooks/protected-files.py` (PreToolUse hook for Edit/Write/NotebookEdit)
- Cost default: Haiku; escalate to Sonnet only when reasoning required
- Secrets never touched — PreToolUse `.claude/hooks/secret-scanner.py` blocks Bash, GitHub writes, and file writes (Edit/Write/NotebookEdit) that contain credential values; credential paths themselves are denied by `permissions.deny` globs in `~/.claude/settings.json`

## 9. Project structure

```
jarvis/
├── config/
│   └── SOUL.md              ← Jarvis personality (canonical; copied by hand to ~/.claude/SOUL.md)
├── .claude-userlevel/       ← SOURCE OF TRUTH for universal skills only (no installer)
│   └── skills/              ← universal skills, copied by hand to ~/.claude/skills/
├── scripts/                 ← project-local automation (gates, reports, hooks not tied to Edit/Write)
├── src/
│   └── risk_radar.py        ← Standalone risk scanner (no LLM)
├── tests/                   ← pytest suite
├── docs/
│   ├── architecture.md      ← This file
│   ├── security/
│   │   └── agent-boundaries.md  ← Protected-file + scope rules (single source)
│   └── design/              ← Design notes per pillar
├── .claude/                 ← Project-scoped (see .claude/README.md)
│   ├── README.md            ← Points to .claude-userlevel/ for universal-skill source
│   ├── hooks/                ← secret-scanner.py, protected-files.py, device-info.py
│   ├── settings.json        ← PreToolUse/SessionStart hook registrations
│   ├── agents/coding.md     ← Project-scoped coding subagent
│   └── skills/sprint-report/  ← Only non-universal skill
├── CLAUDE.md                ← Jarvis-project session rules (@AGENTS.md import)
├── AGENTS.md                ← Process rules (cross-tool standard)
├── .github/workflows/       ← CI
├── .env.example
└── pyproject.toml
```

There is no installer and no `.mcp.json` anywhere in this layout. MCP servers are registered per-device directly against Claude Code with `claude mcp add --scope user <name> ...`; a fresh device gets the SOUL/skills/hooks by copying the files above into `~/.claude/` by hand.
