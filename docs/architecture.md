# Jarvis Architecture

Version: 4.1
Date: 2026-04-24
Status: Active

## 1. System Overview

Jarvis is a personal AI agent built on top of **Claude Code** — not a custom Python application. Claude Code is the runtime; Jarvis adds identity, memory, and skills on top of it.

Since EPIC #335 (2026-04-23), Jarvis is **federated** to user level: the SOUL, the core skills, the hooks, and the MCP servers live at `~/.claude/` and load regardless of which project Claude Code was launched in. Project repos only carry project-specific additions.

```
┌──────────────────────────────────────────────────────┐
│                     Claude Code                       │
│                                                       │
│  ~/.claude/SOUL.md       ← Jarvis identity            │
│  ~/.claude/skills/       ← universal slash commands   │
│  ~/.claude/settings.json ← hooks (SessionStart, ...)  │
│  ~/.claude/.mcp.json     ← MCP servers (user-level)   │
│                                                       │
│  <project>/CLAUDE.md     ← project rules              │
│  <project>/.claude/      ← project-specific skills    │
│                            + agents (e.g. coding.md)  │
│                                                       │
│  MCP Servers:                                         │
│  ├── memory   ← Supabase (this repo)                  │
│  ├── github   ← official MCP                          │
│  └── context7 ← live library docs                     │
└──────────────────────┬───────────────────────────────┘
                       │
                  Supabase DB
           (memory syncs across all devices)
```

## 2. What lives where

### User-level (universal, one install per device)

Installed to `~/.claude/` by `scripts/install/installer.py` (entry points `install.ps1` / `install.sh`). Source of truth for most of it lives in this repo under `.claude-userlevel/`; SOUL stays canonical at `config/SOUL.md`.

| Component | Source in repo | Installed to | Purpose |
|-----------|----------------|--------------|---------|
| Identity | `config/SOUL.md` | `~/.claude/SOUL.md` | Personality, tone, behavior rules (loaded by SessionStart) |
| Universal skills | `.claude-userlevel/skills/*/SKILL.md` | `~/.claude/skills/*/SKILL.md` | Core slash commands: `implement`, `delegate`, `verify`, `status`, `reflect`, `end` (with `--quick` flag), `research`, `goals`, `self-improve`, `setup-tasks`. (`autonomous-loop` retained on disk but SUPERSEDED 2026-05-26 — do not invoke for new flows.) |
| Hooks | `.claude-userlevel/settings.json` | `~/.claude/settings.json` (deep-merged) | SessionStart, PreCompact, PreToolUse secret/dedup/protected-file scans, UserPromptSubmit memory recall |
| MCP servers | `.claude-userlevel/.mcp.json` | `~/.claude/.mcp.json` (deep-merged) | memory, github, context7, etc. |
| Version pin | — | `~/.claude/.jarvis-version` | Current applied jarvis SHA (for no-op detection) |

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
| Memory server | `mcp-memory/server.py` | Cross-device Supabase memory via MCP |
| Installer | `scripts/install/installer.py` | Seeds `~/.claude/` from this repo; idempotent, backup-first |
| Hook scripts | `scripts/*.py` | SessionStart context, PreCompact backup, secret scanner, protected-file guard, memory recall |
| Risk scanner | `src/risk_radar.py` | Deterministic pattern scan, no LLM |

Everything else (Telegram, scheduling, background tasks) uses Anthropic-native features — not custom code.

## 3. Memory architecture

Cross-device memory is the core value-add over vanilla Claude Code.

```
Device A (home)          Device B (work)          Device C (laptop)
     │                        │                        │
     └────────────────────────┼────────────────────────┘
                              │
                    mcp-memory/server.py
                    (runs in .venv, stdio)
                              │
                         Supabase DB
                    (pgvector + VoyageAI)
```

**How it works:**
- `memory_store` — upsert by `(project, name)`, overwrites on conflict
- `memory_recall` — semantic search via VoyageAI embeddings; falls back to ILIKE keyword search if `VOYAGE_API_KEY` not set
- `memory_list` / `memory_get` / `memory_delete` — standard CRUD

**Memory types:** `user`, `project`, `decision`, `feedback`, `reference`

**Scoping:** `project=null` for cross-project (owner preferences, agent rules), `project="jarvis"` or `project="redrobot"` for project-specific context.

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

Universal skills live at `~/.claude/skills/` (source of truth: `.claude-userlevel/skills/`) and are invoked as `/skill-name` from any CWD. The routing table in `CLAUDE.md` describes when each is used.

| Skill | Purpose |
|-------|---------|
| `/implement` | Deliver a single GitHub issue in this session |
| `/delegate` | Dispatch multiple issues to parallel coding subagents |
| `/verify` | Check pending outcomes: PRs merged, tests pass, extract lessons |
| `/status` | Project dashboard: git, PRs, issues, CI, risks, goal alerts |
| `/reflect` | Learning loop — review decisions, check outcomes via PRs |
| `/end` (with `--quick`) | Session closure (full reconciliation / 30-sec checkpoint with `--quick`) |
| `/research` | Topic investigation, option comparison, autonomous discovery |
| `/goals` | View / set / update strategic goals |
| `/self-improve` | Health check + gap analysis + auto-apply low-risk fixes |
| `/setup-tasks` | Bootstrap scheduled tasks on a new device (idempotent) |
| ~~`/autonomous-loop`~~ | **SUPERSEDED 2026-05-26.** No live cron. Retained as opt-in pre-M44 catch-up only; will be deleted when reactive-core M44 ships. |

Project-specific skills stay under `<project>/.claude/skills/`. In this repo the only one is `/sprint-report` (redrobot release flow).

## 6. Mobile access

Telegram via **Claude Code Channels** (official Anthropic plugin) — no custom relay code.

Setup: `claude --channels plugin:telegram@claude-plugins-official`

See `docs/telegram-setup.md` for full guide.

## 7. Scheduling

Recurring tasks via **Claude Code `/loop`** or Desktop scheduled tasks — no custom scheduler.

Nightly research runs at 03:00, topics configured in `config/research-topics.yaml`.

## 8. Safety baseline

- Coder subagent: branch + PR only, never direct push to `main`
- Human review required before merge
- Protected-file list — canonical in `docs/security/agent-boundaries.md`; enforced at runtime by `scripts/protected-files.py` (PreToolUse hook for Edit/Write/NotebookEdit)
- Cost default: Haiku; escalate to Sonnet only when reasoning required
- Secrets never touched — PreToolUse `scripts/secret-scanner.py` blocks Bash, GitHub writes, and memory_store calls that contain credential values

## 9. Project structure

```
jarvis/
├── config/
│   ├── SOUL.md              ← Jarvis personality (canonical; installed to ~/.claude/SOUL.md)
│   ├── SETUP.md             ← First-time device setup
│   └── repos.conf           ← Repos scanned by risk-radar (and historically by autonomous-loop, superseded)
├── .claude-userlevel/       ← SOURCE OF TRUTH for user-level install
│   ├── settings.json        ← Hooks (installed to ~/.claude/settings.json)
│   ├── .mcp.json            ← MCP servers (installed to ~/.claude/.mcp.json)
│   └── skills/              ← 12 universal skills (installed to ~/.claude/skills/)
├── scripts/
│   ├── install/
│   │   ├── installer.py     ← Seeds ~/.claude/ from this repo
│   │   └── install-manifest.yaml  ← Whitelist of what ships
│   ├── session-context.py   ← SessionStart: load memory + goals
│   ├── memory-recall-hook.py  ← UserPromptSubmit: topic-aware recall
│   ├── secret-scanner.py    ← PreToolUse: block credential values
│   ├── protected-files.py   ← PreToolUse: block edits to protected files
│   ├── pre-compact-backup.py  ← PreCompact: snapshot before summarization
│   └── device-info.py       ← SessionStart: banner
├── mcp-memory/
│   ├── server.py            ← MCP memory server (Supabase)
│   ├── schema.sql           ← Supabase table + vector index
│   └── requirements.txt
├── src/
│   └── risk_radar.py        ← Standalone risk scanner (no LLM)
├── tests/                   ← pytest suite (800+ tests)
├── docs/
│   ├── PROJECT_PLAN.md      ← Vision, milestones
│   ├── architecture.md      ← This file
│   ├── security/
│   │   └── agent-boundaries.md  ← Protected-file + scope rules (single source)
│   └── design/              ← Design notes per pillar
├── .claude/                 ← Project-scoped (tombstoned — see .claude/README.md)
│   ├── README.md            ← Tombstone pointer
│   ├── settings.json        ← `{}` — reserved for project-local hooks
│   ├── agents/coding.md     ← Project-scoped coding subagent
│   └── skills/sprint-report/  ← Only non-universal skill
├── install.ps1              ← Windows entry point to installer.py
├── install.sh               ← POSIX entry point
├── CLAUDE.md                ← Jarvis-project session rules
├── .github/workflows/       ← CI
├── .mcp.json                ← Project MCP registry (repo-scoped extras)
├── .env.example
└── pyproject.toml
```

After `install.ps1 -Apply` (or `install.sh --apply`), user-level artefacts land under:

```
~/.claude/
├── SOUL.md                  ← copied from config/SOUL.md
├── settings.json            ← deep-merged from .claude-userlevel/settings.json
├── .mcp.json                ← deep-merged from .claude-userlevel/.mcp.json
├── skills/                  ← 12 universal skills
└── .jarvis-version          ← git SHA of applied jarvis version
```
