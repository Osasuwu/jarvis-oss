# Jarvis

Personal AI agent built on [Claude Code](https://claude.ai/code) + [MCP](https://modelcontextprotocol.io/).

> **Jarvis -- autonomous engineering peer for one principal: sees the full picture, works while you sleep, argues when you're wrong, and gets more accurate every day.**

Not a tool, not an assistant. A peer-role with asymmetric responsibilities: the principal owns taste, stop-decisions, and physical-world interface; Jarvis owns breadth, persistence, and implementation. A solo developer lacks not hands but **breadth** -- Jarvis compensates: tracking, researching, monitoring, remembering, prioritizing. See [`docs/VISION.md`](docs/VISION.md) for the full framing.

> **Status:** v0.7.0 -- core memory + skills working. [Capability map below](#capabilities).

## Quick Start

This repo is a **GitHub template**. Click **"Use this template"** to create your own
copy under your account, then clone that copy (replace `your-username`):

```bash
git clone https://github.com/your-username/jarvis.git
cd jarvis
```

Then follow [`docs/setup.md`](docs/setup.md) for the full walkthrough — Python env,
`.env` secrets, Supabase schema, MCP registration, plugin install (~15 minutes).
New here? Start with [ONBOARDING.md](ONBOARDING.md) first.

After setup, open in Claude Code — session context (git, PRs, issues, CI, risks, goals) loads automatically via the SessionStart hook.

### Prerequisites

> **Cost of entry:** Claude Code requires a paid Claude.ai plan (Pro, Max, or Team) or Anthropic API billing — there is no free tier. Budget this before cloning.

- [Claude Code](https://claude.ai/code) installed and authenticated
- Python 3.11+
- Node.js 18+ (for MCP servers via `npx`)
- [Supabase](https://supabase.com) account (free tier) — optional, powers a few
  auxiliary features (`comm_patterns`, `credential_registry`, `audit_log`,
  `review_debt`); core memory is native/file-based and needs no database
- [GitHub CLI](https://cli.github.com) (optional, for GitHub MCP)

## Architecture

```
You (any device)
  |
  |-- Claude Code CLI / Desktop / Web
  |     |
  |     |-- ~/.claude/skills/      universal slash commands (user-level, CWD-agnostic)
  |     |-- ~/.claude/SOUL.md      personality (auto-loaded)
  |     |-- ~/.claude/settings.json    hooks (SessionStart, PreToolUse, ...)
  |     |-- ~/.claude/projects/<project>/memory/    native memory (per-machine, file-based)
  |     |-- MCP servers registered via `claude mcp add --scope user` (github, obsidian, ...)
  |     |
  |     |-- jarvis/CLAUDE.md       project rules + autonomy config
  |     |-- jarvis/.claude/        project-scoped extras (plan-review agents, hooks)
  |     |
  |-- Telegram (via Claude Code Channels, optional)

Supabase DB -- optional, not memory (memory is native/file-based, see below)
  |-- comm_patterns, credential_registry, audit_log, review_debt, goals, ...
```

The custom installer that used to sync `.claude-userlevel/` into `~/.claude/`
has been retired — Claude Code's own hooks, MCP registrations, and settings
live at user level directly now, not as a build artifact of a repo-side
installer. Source of truth for user-level skills is still
[`.claude-userlevel/skills/`](.claude-userlevel/skills/) in this repo; see
[`docs/setup.md`](docs/setup.md) for how to wire `~/.claude/` up manually
(MCP registration, plugin list, skills).

**Design principle:** Claude Code native first -- skills, hooks, subagents, and native auto-memory; custom Python is justified on merit (see [`docs/reference/native-first-substrate.md`](docs/reference/native-first-substrate.md)).

## What's Working

| Component | Description |
|-----------|-------------|
| **Native memory** | File-based, per project, under `~/.claude/projects/<project>/memory/`. No server, no sync. |
| **Core skills** | `/implement`, `/dispatch`, `/research`, `/end` (`--quick` for fast exit). |
| **SOUL.md personality** | Auto-loaded every session via hook. Opinionated, direct. |
| **Goal-aware decisions** | Jarvis knows priorities and pushes back when a task conflicts with active goals |
| **Dispatch pipeline** | Issue -> `agent:dispatch` label -> `agent-dispatch.yml` workflow -> `claude-code-action` -> PR queued for auto-merge |
| **Setup guide** | [`docs/setup.md`](docs/setup.md) -- manual walkthrough, validates prerequisites |

## Skills

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `/implement` | "implement #42" | Issue → branch → inline implementation → PR (main session does the work) |
| `/dispatch` | "dispatch #X #Y to agents" | Issue → readiness gate → `agent:dispatch` label → `agent-dispatch.yml` runs `claude-code-action` headless → PR queued for auto-merge |
| `/research` | "research X", "compare A vs B" | Web research with source validation |
| `/end` | End of session | Behavioral reflection, decision log, memory save, commit. With `--quick`: checkpoint + commit only (~30 sec). |

## Memory System

Memory is native and file-based, per project, under `~/.claude/projects/<project>/memory/`:
`MEMORY.md` is the always-loaded index (one line per fact, pointing at a topic file), and
`decisions.md` is the dated decision journal. There is no memory service and no recall tool --
reading a file is the recall. It is per-machine, not synced across devices.

Memory types: `user`, `project`, `decision`, `feedback`, `reference`

## Capabilities

The architecture has two complementary groupings:

- **Vision pillars** — 8 narrative tracks + Digital Twin mode, organized around the [Five Axes](docs/VISION.md#five-axes) (what it knows / wants / thinks / does / learns). Stable framing for «what Jarvis is».
- **Capabilities (caps)** — 18 implementation units grouped into engineering layers (Identity / Cognition / Action / Interface / Cross-cutting). Stable framing for «what we build». This is the structural unit — sprints close caps, not pillars.

The cap-to-axis grouping below is a quick map; pillar membership is intentionally narrative-only (per [pillars vs caps decision](docs/VISION.md#implementation-pillars)). Live migration progress lives in your own GitHub milestones, not here.

| Axis (Vision) | Capabilities |
|---|---|
| What it knows — World Model | C3 Memory store, C11 Perception, C17 Observability |
| What it wants — Goals | C2 Goals & priorities |
| How it thinks — Judgment & Identity | C1 Identity & values, C4 Reasoning & planning, C6 Decision gating, C18 Proactive challenger |
| How it acts — Execution | C7 Execution, C8 Sub-orchestration, C9 Tool / environment interface, C10 Research, C12 Communication, C13 Budget, C14 Security & privacy, C16 Verification |
| How it learns — Outcomes & Reflection | C5 Reflection / learning, C15 Self-improvement |

Full capability detail, migration order, and bootstrap protocol: [docs/design/jarvis-v2-redesign.md](docs/design/jarvis-v2-redesign.md). Vision: [docs/VISION.md](docs/VISION.md). Active sprint scope: your own GitHub milestones (`https://github.com/your-username/jarvis/milestones`).

## Project Structure

```
jarvis/
  CLAUDE.md              <- agent rules (auto-loaded by Claude Code)
  AGENTS.md              <- process rules for agents working in this repo
  config/
    SOUL.md              <- personality definition
    repos.conf           <- repos to scan
  .claude/
    skills/              <- project-scoped slash commands
    agents/              <- plan-review critic panel (planner, critic-goal-fit,
                            critic-state-fit, critic-tiebreak, coding)
    settings.json        <- project hooks
  supabase/
    schema.sql           <- declarative target shape (goals, comm_patterns,
                            credential_registry, audit_log, review_debt, ...)
    migrations/          <- apply-order DDL history
  docs/                  <- vision, architecture, guides
```

## Using on Multiple Devices

1. Clone your own copy of this template
2. Follow [`docs/setup.md`](docs/setup.md)
3. Open in Claude Code
4. (Optional) Register scheduled automation (daily briefs, risk radar, etc.) via the scheduled-tasks MCP tools

Memory does **not** sync across devices today — it's native/file-based and per-machine (see [Memory System](#memory-system) above). All config lives in the repo.

## Contributing

This is a personal-agent template — once you create your own copy, track work in
your own repo's issues and milestones (`https://github.com/your-username/jarvis`).

## License

MIT -- see [LICENSE](LICENSE).
