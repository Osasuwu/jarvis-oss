# Onboarding — first-time setup

This is a **personal AI agent** built on Claude Code. You are standing up **your own
instance** — your own repos, your own tokens, your own `~/.claude/` config. Nothing here
connects back to anyone else's services. Work top to bottom; it takes about 15-20 minutes.

Prerequisites: [Claude Code](https://claude.ai/code) (authenticated), Python 3.11+ with
[`uv`](https://docs.astral.sh/uv/), Node.js 18+, [GitHub CLI](https://cli.github.com), and
(optional) a free [Supabase](https://supabase.com) account.

---

## 0. Get your own copy

This repo is a **GitHub template**. Click **"Use this template" → "Create a new
repository"** to get a copy under your own account. Everything below assumes you are
working in *your* copy, not the original.

```bash
git clone https://github.com/your-username/jarvis.git
cd jarvis
```

---

## 1. Fill the template slots

The agent's identity lives in [`config/SOUL.md`](config/SOUL.md). It ships with
`{{DOUBLE_BRACE}}` placeholders you replace **once**, before the first run — that's the
only file that uses the identity slots.

| Slot | Default | What it is |
|---|---|---|
| `{{AGENT_NAME}}` | `Jarvis` | What the agent calls itself |
| `{{PRINCIPAL_NAME}}` | `the user` | How the agent refers to you |
| `{{PRINCIPAL_LANGUAGES}}` | `English` | Language(s) you write in (e.g. `English`, or `Russian or English`) |

Set them by hand to taste (leave a slot on its default if it already fits, then delete the
template-slots comment block at the top of `SOUL.md`).

```bash
# example one-pass replace (adjust to your shell); do this before first run
sed -i 's/{{AGENT_NAME}}/Jarvis/g; s/{{PRINCIPAL_NAME}}/Alex/g; s/{{PRINCIPAL_LANGUAGES}}/English/g' config/SOUL.md
```

---

## 2. Set up the Python environment

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
locked dependencies from `uv.lock`. Fill in the copied `.env` per the next step.

---

## 3. (Optional) Stand up Supabase

Core memory is native and file-based — no database needed for it. Supabase is optional
and only powers a few auxiliary features (`comm_patterns`, `credential_registry`,
`audit_log`, `review_debt`, `goals`).

1. Create a free project at [supabase.com](https://supabase.com).
2. Apply the migrations under `supabase/migrations/` (in filename order) via the SQL
   editor or CLI — `supabase/schema.sql` is the declarative target shape they converge on.
3. From **Project Settings → API**, copy the project URL and keys into `.env`:

| `.env` var | Where to get it |
|---|---|
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_KEY` | Project Settings → API → `service_role` key (server-side) |

This DB is **yours alone**. Skip this step entirely if you don't need the auxiliary
features — everything else still works.

---

## 4. Add your tokens

Fill the rest of `.env` (see [`.env.example`](.env.example) for the full list):

| `.env` var | Purpose | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API access | yes |
| `GITHUB_TOKEN` | GitHub MCP server (issues, PRs) | yes, for repo work |
| `FIRECRAWL_API_KEY` | Web research (`/research`) | optional |

> **Never commit `.env`.** It's gitignored. Secrets go in `.env` (local) and GitHub
> Actions secrets (CI) — never in tracked files, issues, or commits.

---

## 5. Make `~/.claude/` your own

Jarvis used to ship an installer that synced skills, hooks, and MCP config from this
repo's `.claude-userlevel/` into `~/.claude/` on every device. That installer has been
retired — the target model is: `~/.claude/` is *your own* private dotfiles repo, which you
create and version yourself (like a personal `dotfiles` repo for shell config), not
something synced in from this repo by a script.

Copy what you want from [`.claude-userlevel/skills/`](.claude-userlevel/skills/) (the
source of truth for user-level skills) into your own `~/.claude/skills/`, and use
[`config/SOUL.md`](config/SOUL.md) as the template for your own `~/.claude/SOUL.md`. Put
`~/.claude/` under `git` and adapt it to your own setup.

Then register MCP servers by hand — see
[`docs/setup.md` §5](docs/setup.md#5-manual-mcp-registration-checklist) for the exact
commands (GitHub MCP, optional Obsidian).

---

## 6. GitHub App for CI review (optional but recommended)

The repo's PR-review automation (`.github/workflows/code-review.yml`) runs Claude on your
PRs. To wire it up in your repo, add a repo Actions secret `CLAUDE_CODE_OAUTH_TOKEN` (from
your Claude account) — used by `anthropics/claude-code-action`. See
[`docs/setup.md` §8](docs/setup.md#8-github-actions-secrets-if-you-run-this-repos-ci) for
the full secret list. If you skip this, everything still works locally — you just review
your own PRs by hand.

---

## 7. Point it at your repos

`config/repos.conf` ships generic. Add the repositories you want the agent to track — one
per line. The agent scans these for `/status`-equivalent context, risk radar, and dispatch.

---

## 8. Verify

```bash
cd jarvis && claude
```

Then in the session:

- Check skills loaded — type `/` and confirm `/implement`, `/dispatch`, etc. appear.
- Confirm SOUL loaded — the agent should introduce itself with your `{{AGENT_NAME}}`, not
  the literal `{{...}}` token. If you still see braces, you skipped step 1.

For the full walkthrough (plugins, Telegram, lockfile regeneration, validation checklist),
see [`docs/setup.md`](docs/setup.md).

---

## What's *not* shared

This is a clean personal instance. It does **not** carry over anyone else's memory,
goals, outcomes, credentials, or private repos.

Design docs under `docs/design/`, `docs/adr/`, and `docs/decisions/` may reference the
original `Osasuwu/jarvis` project as documented heritage — that's provenance, not a live
dependency. You own everything from here.
