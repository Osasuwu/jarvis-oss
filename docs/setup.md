# Setup — Jarvis

Single setup guide for a new device: clone, secrets, MCP servers, plugins. Takes ~15
minutes. Supersedes the old `SETUP.md` / `config/SETUP.md` / `docs/telegram-setup.md`.

> **Cost of entry:** Claude Code requires a paid Claude.ai plan (Pro, Max, or Team) or
> Anthropic API billing — there is no free tier that covers it. Budget this before
> starting.

## Prerequisites

- [Claude Code](https://claude.ai/code) installed and authenticated (`claude --version`)
- [GitHub CLI](https://cli.github.com) installed and authenticated (`gh auth status`)
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (`pip install uv`)
- [Supabase](https://supabase.com) account (free tier is enough) — optional, powers a few
  auxiliary features (`comm_patterns`, `credential_registry`, `audit_log`, `review_debt`);
  memory itself is native, file-based (`~/.claude/projects/<project>/memory/`), not
  Supabase-backed
- Node.js 18+ (some MCP servers run via `npx`)
- Windows 11 (primary), Linux/macOS also supported

## 1. Clone and configure

This repo is a **GitHub template**. Click **"Use this template"** to create your own
copy under your account, then clone that copy (replace `your-username`):

```bash
git clone https://github.com/your-username/jarvis.git
cd jarvis
```

**Edit `config/repos.conf` before running `/triage`** — it ships with a generic
placeholder. Replace it with your own repos (`owner/repo` format, one per line) so skill
output refers to your projects, not someone else's.

```bash
# Windows
notepad config\repos.conf

# Linux / macOS
nano config/repos.conf
```

## 2. Create the Python environment

```bash
uv sync --project .
```

```bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
```

`uv sync` is idempotent — safe to re-run anytime — and creates `.venv/`, installing the
locked dependencies from `uv.lock`. Fill in the copied `.env` per [§3](#3-fill-in-secrets-env)
below. Install the Claude Code plugins listed in [§6](#6-plugins) separately — the
vendored fork from `.claude/marketplace`, the rest from the official Anthropic
marketplace.

## 3. Fill in secrets (`.env`)

Minimum required values:

```env
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-key-here
```

> **Where to get Supabase credentials:** Supabase dashboard → your project → Settings →
> API → Project URL + anon public key.

Apply the migrations under `supabase/migrations/` (in filename order) to your project via the
Supabase SQL Editor or CLI to create the reactive-core tables (task queue, events, sandcastle).
`supabase/schema.sql` is the declarative target shape those migrations converge on, not a
from-scratch bootstrap script — see [MCP & environment](reference/mcp-and-environment.md).

Optional, depending on what you use:

- `GITHUB_TOKEN` — for the `github` MCP server (see [§5](#5-manual-mcp-registration-checklist))
- `FIRECRAWL_API_KEY` — for web research

## 4. `~/.claude/` — make it your own private dotfiles repo

Jarvis used to ship an installer (`install.ps1` / `install.sh` /
`scripts/install/installer.py`) that synced skills, hooks, and MCP config from this
repo's `.claude-userlevel/` into `~/.claude/` on every device. **That installer was
retired in [#1800](https://github.com/Osasuwu/jarvis/issues/1800).** Per decision
[`57fd2895`](https://github.com/Osasuwu/jarvis), the target model is: `~/.claude/` is
*your own* private dotfiles repo, which you create and version yourself (like a
personal `dotfiles` repo for shell config) — not something synced in from
`jarvis/.claude-userlevel/` by a script.

Copy what you want from [`.claude-userlevel/skills/`](../.claude-userlevel/skills/)
(the only thing left under `.claude-userlevel/` — the source of truth for user-level
skills) into your own `~/.claude/skills/`, and use [`config/SOUL.md`](../config/SOUL.md)
as the template for your own `~/.claude/SOUL.md`. Put `~/.claude/` under `git` and adapt
it to your own setup. The manual MCP registration checklist below is exactly what
replaces what the legacy installer would otherwise have auto-seeded.

## 5. Manual MCP registration checklist

Memory is native and file-based (`~/.claude/projects/<project>/memory/`) — no server to run
or verify. The `mcp-memory`/`mcp-status`/`mcp-morning` project-local MCP servers this section
used to describe were retired in [#1801](https://github.com/Osasuwu/jarvis/issues/1801); the
`telegram` plugin ([§7](#7-telegram-optional)) is the one remaining MCP-adjacent surface, and
it's a Claude Code Channels plugin, not a script you launch. Register the following by hand:

- [ ] **`github`** — HTTP transport, GitHub's own remote MCP endpoint:

  ```bash
  claude mcp add --transport http --scope user github https://api.githubcopilot.com/mcp \
    --header "Authorization: Bearer $GITHUB_TOKEN"
  ```

  (`$GITHUB_TOKEN` from your `.env`, or substitute `$(gh auth token)`.)

- [ ] **`obsidian`** — *device-gated*: only register this on a device where you actually
  keep an Obsidian vault. Skip it entirely otherwise.

  ```bash
  claude mcp add --transport stdio --scope user --env OBSIDIAN_VAULT_PATH="/path/to/your/vault" \
    obsidian -- npx -y @bitbonsai/mcpvault@latest "$OBSIDIAN_VAULT_PATH"
  ```

- [ ] Verify both: `claude mcp list` should show `github` and (if registered) `obsidian`
  as connected.

## 6. Plugins

Install commands below for each plugin:

| Plugin | Source | Install |
|---|---|---|
| `code-review` | **Fork** of `anthropics/claude-plugins-official`, tag-pinned in this repo's `.claude/marketplace/` ([`docs/reference/vendored-plugin-pins.md`](reference/vendored-plugin-pins.md)). Kept forked: upstream silently drops review results when a sub-reviewer runs backgrounded under headless CI (`claude -p`) — jarvis#1239 / PR #1237. | `claude plugins marketplace add ./.claude/marketplace` (from the repo root), then `/plugin install code-review@jarvis-fork-plugins` |
| `pr-review-toolkit` | Official Anthropic marketplace, unmodified | `/plugin install pr-review-toolkit@claude-plugins-official` |
| `session-report` | Official Anthropic marketplace, unmodified | `/plugin install session-report@claude-plugins-official` |
| `hookify` | Official Anthropic marketplace, unmodified | `/plugin install hookify@claude-plugins-official` |
| `claude-md-management` | Official Anthropic marketplace, unmodified | `/plugin install claude-md-management@claude-plugins-official` |
| `mcp-server-dev` | Official Anthropic marketplace, unmodified | `/plugin install mcp-server-dev@claude-plugins-official` |

If `/plugin install <id>@claude-plugins-official` fails with an unknown-marketplace
error, the official marketplace isn't registered on your device yet — check with
`claude plugins marketplace list`, and if it's missing, add it with
`claude plugins marketplace add anthropics/claude-plugins-official` first.

`telegram` is also an official-marketplace plugin, but it isn't part of the classified
list above (it's not in `.claude/marketplace/`) — see [§7](#7-telegram-optional).

## 7. Telegram (optional)

Jarvis uses [Claude Code Channels](https://code.claude.com/docs/en/channels) — the
official Anthropic plugin — to connect to Telegram. No custom relay needed.

1. Create a bot via [@BotFather](https://t.me/BotFather): `/newbot`, pick a display name
   and a username ending in `bot`. BotFather gives you a token like
   `123456789:AAHfiqksKZ8...`.
2. Install the plugin: `/plugin install telegram@claude-plugins-official`, then
   `/reload-plugins`.
3. Set the token:
   ```bash
   mkdir -p ~/.claude/channels/telegram
   echo "TELEGRAM_BOT_TOKEN=123456789:AAHfiqksKZ8..." > ~/.claude/channels/telegram/.env
   ```
   (or export `TELEGRAM_BOT_TOKEN` as a shell variable — takes precedence over the file)
4. Start with Channels: `claude --channels plugin:telegram@claude-plugins-official`
5. Pair your account: `/telegram:access pair` in the Claude Code session → send the code
   to your bot in Telegram → back in the session, run
   `/telegram:access policy allowlist` to lock access to just your paired account.
6. Test: message your bot from Telegram — Claude should respond.

For 24/7 availability, run step 4 on one always-on machine (home PC, server, or VPS) —
Channels runs on whichever machine has an active session; native memory files are
per-machine, not shared across devices by this mechanism.

**Troubleshooting:** bot silent → confirm the session is running with `--channels` and
the token has no extra whitespace. "Plugin not found" → run `/reload-plugins` after
install. Unauthorized senders getting through → re-run
`/telegram:access policy allowlist`.

> **`TELEGRAM_ALLOW_USER_ID` is a different thing** — it is *not* part of Channels
> pairing above. It was the target chat id read by the orchestrator-escalation notifier,
> `agents/notify.py`, demolished along with the rest of reactive-core in #1802 with no
> replacement yet — used when jarvis needed to page you outside of an active session.
> Currently unused; unrelated to whether Channels pairing succeeded.

## 8. GitHub Actions secrets (if you run this repo's CI)

Required secrets in GitHub repo settings (Settings → Secrets and variables → Actions):

| Secret | Used by | Purpose |
|--------|---------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` | `code-review.yml` | `anthropics/claude-code-action` for PR review |

`GITHUB_TOKEN` is auto-provisioned by GitHub Actions — no setup needed. `PROJECT_SYNC` is
optional — falls back to `github.token` if not set.

## 9. Cloud scheduled tasks

Scheduled tasks on claude.ai use connectors only, not project-local MCP config. Skills
are designed to work in both environments: locally via native memory files, in the cloud
via the Supabase connector's `execute_sql` and the `gh` CLI.

Task prompts should invoke skills via slash command — this resolves against whichever
Claude Code home (`~/.claude/`) is loaded:

```
Run /research
```

`/research` selects discovery mode automatically when no topic argument is supplied.
Updating a skill in your own `~/.claude/` automatically updates scheduled-task behavior.

## 10. Lockfile regeneration

CI installs from `uv.lock` to guarantee reproducible dependency resolution.
`.github/workflows/dependabot-lockfile.yml` regenerates it automatically when Dependabot
bumps a range in `pyproject.toml`. For manual regeneration (e.g. adding a dependency
locally):

```bash
uv lock --project .
```

Commit the regenerated `uv.lock` files — this ensures CI and local environments resolve
to byte-identical packages.

## Validation checklist

```bash
# Python dependencies
python -c "import mcp, supabase, httpx; print('deps OK')"

# Supabase connection
python -c "
from dotenv import load_dotenv; load_dotenv()
import os; from supabase import create_client
c = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
print('Supabase OK:', c.table('task_queue').select('id').limit(1).execute())
"

# Claude Code + GitHub CLI
claude --version
gh auth status

# MCP servers registered
claude mcp list
```

Then open the project in Claude Code and run `/triage`.

## Key paths

| What | Path |
|------|------|
| Secrets | `.env` (not committed) |
| Secrets template | `.env.example` |
| Personality | `config/SOUL.md` |
| Native memory | `~/.claude/projects/<project>/memory/` |
| Supabase schema (declarative target) | `supabase/schema.sql` |
| Vendored plugin fork + its pin | `.claude/marketplace/`, [`docs/reference/vendored-plugin-pins.md`](reference/vendored-plugin-pins.md) |
| Project-scoped skills (jarvis-only) | `.claude/skills/` |
| User-level skills source of truth | `.claude-userlevel/skills/` (copy into your own `~/.claude/skills/`, see [§4](#4-claude--make-it-your-own-private-dotfiles-repo)) |
