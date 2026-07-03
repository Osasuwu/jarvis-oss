# Onboarding — first-time setup

This is a **personal AI agent** built on Claude Code. You are standing up **your own
instance** — your own memory database, your own tokens, your own repos. Nothing here
connects back to anyone else's services. Work top to bottom; it takes about 30 minutes.

Prerequisites: [Claude Code](https://claude.ai/code) (authenticated), Python 3.11+,
Node.js 18+, a [GitHub](https://github.com) account, and a free
[Supabase](https://supabase.com) account.

---

## 0. Get your own copy

This repo is a **GitHub template**. Click **"Use this template" → "Create a new
repository"** to get a copy under your own account. Everything below assumes you are
working in *your* copy, not the original.

```bash
git clone https://github.com/your-username/jarvis
cd jarvis
```

---

## 1. Fill the template slots

The agent's identity lives in [`config/SOUL.md`](config/SOUL.md). It ships with
`{{DOUBLE_BRACE}}` placeholders you replace **once**, before the first run. Do a single
find-replace pass across the repo (or just edit `config/SOUL.md` — that's the only file
that uses the identity slots).

| Slot | Default | What it is |
|---|---|---|
| `{{AGENT_NAME}}` | `Jarvis` | What the agent calls itself |
| `{{PRINCIPAL_NAME}}` | `the user` | How the agent refers to you |
| `{{PRINCIPAL_LANGUAGES}}` | `English` | Language(s) you write in (e.g. `English`, or `Russian or English`) |
| `{{CLAUDE_USER_HOME}}` | — | Your Claude Code home dir (usually `~/.claude`); set by the installer |
| `{{JARVIS_HOME}}` | — | Absolute path to this repo on the current device; set by the installer |

The first three are identity — set them by hand to taste (leave a slot on its default if
it already fits, then delete the template-slots comment block at the top of `SOUL.md`).
The last two are **path slots filled automatically** by `scripts/setup-device.py` /
`install.ps1` per device — you don't touch those.

```bash
# example one-pass replace (adjust to your shell); do this before first run
sed -i 's/{{AGENT_NAME}}/Jarvis/g; s/{{PRINCIPAL_NAME}}/Alex/g; s/{{PRINCIPAL_LANGUAGES}}/English/g' config/SOUL.md
```

---

## 2. Run device setup

```bash
python scripts/setup-device.py
```

Idempotent — safe to re-run. It creates the Python venv, installs
`mcp-memory/requirements.txt`, copies `.env.example` → `.env`, and validates
prerequisites. Then it seeds the user-level layer (skills, hooks, SOUL) from
`.claude-userlevel/` via `install.ps1` / `install.sh`. Re-run it on **every** device you
use — each device gets its own `{{JARVIS_HOME}}` / `{{CLAUDE_USER_HOME}}`.

---

## 3. Stand up your own Supabase (memory)

Memory is a Supabase Postgres DB — this is what syncs across your devices.

1. Create a free project at [supabase.com](https://supabase.com).
2. Apply the schema: open the SQL editor and run [`mcp-memory/schema.sql`](mcp-memory/schema.sql).
3. From **Project Settings → API**, copy the project URL and keys into `.env`:

| `.env` var | Where to get it |
|---|---|
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_KEY` | Project Settings → API → `service_role` key (server-side) |
| `SUPABASE_ANON_KEY` | Project Settings → API → `anon`/publishable key (used by CI event logging) |

This DB is **yours alone**. It starts empty — the agent builds up its memory of your work
as you use it.

---

## 4. Add your tokens

Fill the rest of `.env` (see [`.env.example`](.env.example) for the full list):

| `.env` var | Purpose | Required? |
|---|---|---|
| `GITHUB_TOKEN` | GitHub MCP server (issues, PRs) | yes, for repo work |
| `VOYAGE_API_KEY` | Semantic memory search (vector embeddings) | see note below |
| `FIRECRAWL_API_KEY` | Web research (`/research`) | optional |

> **Never commit `.env`.** It's gitignored. Secrets go in `.env` (local) and GitHub
> Actions secrets (CI) — never in tracked files, issues, or commits.

### The VoyageAI key (shared)

`VOYAGE_API_KEY` powers vector search over your memories. If someone handed you a shared
key personally, paste it into `.env` and **do not commit it** — it stays in your local
`.env` only, never in the repo. Without a Voyage key the memory server automatically falls
back to keyword search, so this is optional; semantic recall is just better with it.

---

## 5. GitHub App for CI review (optional but recommended)

The repo's PR-review automation (`.github/workflows/code-review.yml`) runs Claude on your
PRs. It expects an installed GitHub App and a token secret. To wire it up in your repo:

1. Create/install a GitHub App on your repo (referred to in configs as `jarvis-ci[bot]` /
   `app/jarvis-ci` — the name is cosmetic; use any name you like).
2. Add a repo Actions secret `CLAUDE_CODE_OAUTH_TOKEN` (from your Claude account) — used by
   `anthropics/claude-code-action`.
3. Add `SUPABASE_URL` and `SUPABASE_ANON_KEY` as Actions secrets too, if you want CI event
   logging into your memory DB.

See [SETUP.md → GitHub Actions secrets](SETUP.md) for the exact secret list. If you skip
this, everything still works locally — you just review your own PRs by hand.

---

## 6. Point it at your repos

`config/repos.conf` ships generic. Add the repositories you want the agent to track — one
per line. The agent scans these for `/status`, risk radar, and delegation.

Also update the "related projects" table in [`CLAUDE.md`](CLAUDE.md) and the fallback hint
in [`config/research-topics.yaml`](config/research-topics.yaml) if you want the nightly
research to know about your second project. Both ship with `your-username/your-repo` /
`your-second-project` placeholders — replace with your real ones.

---

## 7. (Optional) GitHub Project board

Several skills read/write a GitHub Project for issue triage and milestone tracking. If you
use one, create a Project in your account and grant the token access. Skills degrade
gracefully without it — `/status` and `/implement` work off plain issues/PRs.

---

## 8. Verify

```bash
cd jarvis && claude
```

Then in the session:

- Check skills loaded — type `/` and confirm `/status`, `/implement`, etc. appear.
- Run `/status` — it should render your repo's git/PR/issue state.
- Confirm SOUL loaded — the agent should introduce itself with your `{{AGENT_NAME}}`, not
  the literal `{{...}}` token. If you still see braces, you skipped step 1.

---

## What's *not* shared

This is a clean personal instance. It does **not** carry over anyone else's memory,
goals, outcomes, credentials, or private repos. Accumulated engineering lessons that were
worth keeping are synthesized generically in [`docs/LESSONS.md`](docs/LESSONS.md) — read
that for the "why things are the way they are" without needing anyone's private history.

Design docs under `docs/design/`, `docs/adr/`, and `docs/decisions/` reference the
original `Osasuwu/jarvis` project as documented heritage — that's provenance, not a live
dependency. You own everything from here.
