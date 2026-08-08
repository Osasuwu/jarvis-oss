# Jarvis

Personal AI agent built on [Claude Code](https://claude.ai/code) + [MCP](https://modelcontextprotocol.io/).

> **Jarvis -- autonomous engineering peer for one principal: sees the full picture, works while you sleep, argues when you're wrong, and gets more accurate every day.**

Not a tool, not an assistant. A peer-role with asymmetric responsibilities: the principal owns taste, stop-decisions, and physical-world interface; Jarvis owns breadth, persistence, and implementation. A solo developer lacks not hands but **breadth** -- Jarvis compensates: tracking, researching, monitoring, remembering, prioritizing. See [`docs/VISION.md`](docs/VISION.md) for the full framing.

> **Status:** v0.5.0 -- core memory + skills working. [Capability map below](#capabilities).

## Quick Start

This repo is a **GitHub template**. Click **"Use this template"** to create your own
copy under your account, then clone that copy (replace `your-username`):

```bash
git clone https://github.com/your-username/jarvis.git
cd jarvis
python scripts/setup-device.py
```

New here? Start with [ONBOARDING.md](ONBOARDING.md) for the full first-time setup.

The setup script handles everything interactively:
- Creates Python venv + installs dependencies
- Prompts for Supabase credentials (free tier sufficient)
- Tests the database connection
- Validates all prerequisites and project files

After setup, open in Claude Code and run `/status`.

### Prerequisites

- [Claude Code](https://claude.ai/code) installed and authenticated
- Python 3.11+
- Node.js 18+ (for MCP servers via `npx`)
- [Supabase](https://supabase.com) account (free tier)
- [GitHub CLI](https://cli.github.com) (optional, for GitHub MCP)

## Architecture

```
You (any device)
  |
  |-- Claude Code CLI / Desktop / Web
  |     |
  |     |-- ~/.claude/skills/      12 universal slash commands (user-level, CWD-agnostic)
  |     |-- ~/.claude/SOUL.md      personality (auto-loaded)
  |     |-- ~/.claude/settings.json    hooks (SessionStart, PreToolUse, ...)
  |     |-- ~/.claude/.mcp.json    MCP servers (memory, github, context7, ...)
  |     |
  |     |-- jarvis/CLAUDE.md       project rules + autonomy config
  |     |-- jarvis/.claude/        project-scoped extras (e.g. /sprint-report)
  |     |
  |-- Telegram (via Claude Code Channels, optional)

Supabase DB
  |-- memories    (vector search, graph links)
  |-- goals       (strategic context)
  |-- events      (CI, alerts, deployments)
```

User-level Jarvis is seeded from `.claude-userlevel/` in this repo by
`install.ps1` / `install.sh` (idempotent, backup-first). See
`scripts/install/installer.py`.

**Design principle:** Claude Code native first. The only custom Python is `mcp-memory/server.py` -- everything else uses skills, hooks, and subagents.

## What's Working

| Component | Description |
|-----------|-------------|
| **Cross-device memory** | MCP server syncs memories, goals, events via Supabase. Vector search (Voyage AI) + keyword fallback |
| **Core skills** | `/status`, `/implement`, `/delegate`, `/verify`, `/reflect`, `/research`, `/self-improve`, `/goals`, `/setup-tasks`, `/end` (`--quick` for fast exit). |
| **SOUL.md personality** | Auto-loaded every session via hook. Opinionated, direct, bilingual (RU/EN) |
| **Goal-aware decisions** | Jarvis knows priorities and pushes back when a task conflicts with active goals |
| **Delegation pipeline** | Issue -> branch -> coding agent -> PR, with verification |
| **Setup script** | `python scripts/setup-device.py` -- interactive, validates everything |

## Skills

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `/status` | Session start, "what's happening" | Project dashboard: git, PRs, issues, CI, risks, goals |
| `/implement` | "реализуй #42", "implement #X" | Issue → branch → inline implementation → PR (main session does the work) |
| `/delegate` | "делегируй #X #Y", "раскидай на агентов" | Multiple issues → parallel coding subagents, orchestrator reviews each diff + decides merge |
| `/verify` | "проверь результаты", "post-delegation" | Closes outcome loop: PR merge status, test results, lessons extracted |
| `/reflect` | "что сработало", "уроки" | Reviews recent decisions + outcomes, extracts lessons as feedback memories |
| `/research` | "research X", "compare A vs B" | Web research with source validation |
| `/self-improve` | "improve yourself" | Gap analysis -> ideation -> research -> implementation |
| `/goals` | "goals", "priorities" | View, set, update strategic goals in Supabase |
| `/setup-tasks` | New device bootstrap | Registers all scheduled tasks (idempotent) |
| `/end` | End of session | Behavioral reflection, decision log, memory save, commit. With `--quick`: checkpoint + commit only (~30 sec). |

## Memory System

The MCP memory server (`mcp-memory/server.py`) provides persistent memory across all devices and projects.

| Tool | Description |
|------|-------------|
| `memory_store` | Save/update a memory (upserts by project+name) |
| `memory_recall` | Semantic + keyword search across memories |
| `memory_list` | List all memories (name + description) |
| `memory_get` | Fetch a specific memory by name |
| `memory_delete` | Remove a memory |
| `goal_set` / `goal_list` / `goal_update` | Manage strategic goals |

Memory types: `user`, `project`, `decision`, `feedback`, `reference`

All devices connect to the same Supabase instance. No manual sync.

## Capabilities

The architecture has two complementary groupings:

- **Vision pillars** — 8 narrative tracks + Digital Twin mode, organized around the [Five Axes](docs/VISION.md#five-axes) (what it knows / wants / thinks / does / learns). Stable framing for «what Jarvis is».
- **Capabilities (caps)** — 18 implementation units grouped into engineering layers (Identity / Cognition / Action / Interface / Cross-cutting). Stable framing for «what we build». This is the structural unit — sprints close caps, not pillars.

The cap-to-axis grouping below is a quick map; pillar membership is intentionally narrative-only (per [pillars vs caps decision](docs/VISION.md#implementation-pillars)). Live migration progress lives in GitHub milestones, not here.

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
  .mcp.json              <- MCP server registry
  config/
    SOUL.md              <- personality definition
    repos.conf           <- repos to scan
  .claude/
    skills/              <- project-scoped slash commands (sprint-report)
    agents/              <- subagent definitions (coding)
    settings.json        <- project hooks
  mcp-memory/
    server.py            <- MCP memory server (Supabase)
    schema.sql           <- database schema (memories, goals, events)
  scripts/
    setup-device.py      <- interactive device setup
    session-context.py   <- loads context at session start
  src/
    risk_radar.py        <- standalone risk scan (no LLM)
  docs/                  <- vision, architecture, guides
```

## Using on Multiple Devices

1. Clone the repo
2. Run `python scripts/setup-device.py`
3. Open in Claude Code
4. (Optional) Run `/setup-tasks` to register scheduled automation (daily briefs, risk radar, etc.)

Memory syncs automatically via Supabase. All config lives in the repo.

## Contributing

This is a personal-agent template — once you create your own copy, track work in
your own repo's issues and milestones (`https://github.com/your-username/jarvis`).

## License

MIT -- see [LICENSE](LICENSE).
