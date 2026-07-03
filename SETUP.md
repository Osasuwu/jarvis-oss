# Setup — Jarvis

## Quick start (new device)

This repo is a **GitHub template** — create your own copy via **"Use this template"**,
then clone that copy (replace `your-username` below). First time? See [ONBOARDING.md](ONBOARDING.md).

```bash
git clone https://github.com/your-username/jarvis
cd jarvis
python scripts/setup-device.py
```

The script is idempotent — safe to re-run anytime.

## What the script does

1. **Python venv** — creates `.venv/`, installs `mcp-memory/requirements.txt`
2. **`.env`** — copies from `.env.example` (secrets filled in manually)
3. **Validation** — checks Python packages, env vars, config files, CLI tools

## Manual steps after script

1. **Fill secrets** in `.env`:
   - `SUPABASE_URL` / `SUPABASE_KEY` — required (get from Supabase dashboard)
   - `GITHUB_TOKEN` — for MCP GitHub server
   - `FIRECRAWL_API_KEY` — for web research
   - `VOYAGE_API_KEY` — optional, for semantic memory search

2. **Cloud connectors** (for scheduled tasks on claude.ai):
   - Supabase connector — claude.ai/settings/connectors
   - Firecrawl connector — claude.ai/settings/connectors

3. **Verify**: `cd jarvis && claude` — check skills load, `/status` works

## Architecture

```
jarvis/           <- self-contained, this is Jarvis
├── CLAUDE.md                <- all rules (identity, autonomy, memory, delegation)
├── config/
│   ├── SOUL.md              <- personality
│   ├── repos.conf           <- tracked repos (single source of truth)
│   └── research-topics.yaml <- fallback research hints
├── .mcp.json                <- MCP servers (env vars, no hardcoded paths)
├── .env                     <- secrets (gitignored)
├── .claude/
│   ├── skills/              <- all skills (11 total)
│   ├── settings.json        <- project hooks (git-tracked)
│   └── settings.local.json  <- device overrides (gitignored)
├── mcp-memory/              <- Supabase memory MCP server (Python)
├── scripts/
│   ├── setup-device.py      <- new device setup
│   ├── run-memory-server.py <- cross-platform MCP launcher
│   └── token-refresh.py     <- refresh CI OAuth token across repos
└── .github/workflows/       <- CI/CD
```

## GitHub Actions secrets

Required secrets in GitHub repo settings (Settings → Secrets and variables → Actions):

| Secret | Used by | Purpose |
|--------|---------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` | `code-review.yml` | `anthropics/claude-code-action` for PR review |
| `SUPABASE_URL` | `event-dispatch.yml` | Supabase project URL for event logging |
| `SUPABASE_ANON_KEY` | `event-dispatch.yml` | Supabase publishable anon key for event logging |

`GITHUB_TOKEN` is auto-provisioned by GitHub Actions — no setup needed.

`PROJECT_SYNC` is optional — falls back to `github.token` if not set.

## Cloud scheduled tasks

Scheduled tasks on claude.ai don't load `.mcp.json` — they use connectors only.
Skills are designed to work in both environments:
- Local: `memory_store`/`memory_recall` via custom MCP
- Cloud: `execute_sql` via Supabase connector, `gh` CLI for GitHub

Task prompts should invoke skills via slash command — resolves against whichever Claude Code home (`~/.claude/` after federation) is loaded:
```
Run /research
```
`/research` selects discovery mode automatically when no topic argument is supplied (post-#529). Updating the skill in the repo (via `.claude-userlevel/skills/<name>/` → re-apply the installer) automatically updates task behaviour.
