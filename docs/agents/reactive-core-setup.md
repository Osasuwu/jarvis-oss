# Reactive-core Agents — Dev Setup

Federation & Delegation pillar (milestone #44). The reactive-core loop wakes on
Postgres `LISTEN/NOTIFY`, routes each event through a deterministic orchestrator,
and spawns `claude -p` workers for coding tasks. Agents run alongside Claude Code
(not as a replacement) and share state with it through Supabase — the events,
`task_queue`, memories, goals, and `audit_log` tables are the same rows Claude
Code reads via `memory_recall` / `events_list` / `goal_list`.

This document covers local setup: dependencies, environment, and running the loop.

## Prerequisites

- Python 3.11+ (project requirement)
- A Supabase project (shared knowledge base) **or** a local `supabase start`
  stack — the `events` / `task_queue` NOTIFY triggers that wake the driver live
  in `supabase/migrations/`
- Ollama (optional, staged-dormant — no live consumer yet) — see
  [ollama-setup.md](ollama-setup.md)

## Install dependencies

From repo root:

```bash
pip install -e ".[agents]"
```

This pulls:

| Package | Purpose |
|---------|---------|
| `psycopg[binary,pool]` | Direct-Postgres driver — the `LISTEN/NOTIFY` socket wake_driver opens (the PostgREST client can't `LISTEN`) |
| `supabase` | Agent bridge to the shared knowledge base (memories, events, `task_queue`, goals, `audit_log`) |
| `httpx` | GitHub Events API client |
| `ollama` | Official Python client — **staged-dormant**, no live consumer yet (see [ollama-setup.md](ollama-setup.md)) |

## Configure environment

Copy the relevant lines from `.env.example` into `.env`:

```
# Direct-Postgres session-mode DSN — wake_driver's LISTEN/NOTIFY socket.
AGENTS_POSTGRES_URL=postgresql://postgres:[YOUR-PASSWORD]@db.your-project-ref.supabase.co:5432/postgres

# Supabase bridge — same vars the MCP memory server uses; re-used here.
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<anon-or-service-key>

# Optional — Ollama local inference (staged-dormant, no live consumer yet)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
```

`AGENTS_POSTGRES_URL` needs a **session** connection — a direct
`db.<ref>.supabase.co:5432` or session-pooler `:5432` endpoint, **never** the
transaction pooler `:6543` (transaction mode drops `LISTEN`). The direct
endpoint may be IPv6-only on newer projects. There is **no default**:
`wake_driver._build_psycopg_queue()` raises a clear `RuntimeError` naming the
var if it's unset, mirroring the `SUPABASE_URL` / `SUPABASE_KEY` fail-loud
contract. See `.env.example` for the full comment.

### Local Supabase (optional)

To run against a local stack instead of the cloud project:

```bash
supabase start          # boots local Postgres + PostgREST
supabase db reset       # applies supabase/migrations/ (events + task_queue NOTIFY triggers)
```

Point `AGENTS_POSTGRES_URL` at the local session endpoint (`supabase status`
prints the DB URL). The NOTIFY triggers that wake the driver ship in
`supabase/migrations/` — a bare Postgres without them will never fire a wake.

## Run the loop

```bash
# Long-running: LISTEN on the events channel, drain tasks, watchdog stale rows.
python -m agents.wake_driver

# One-shot tick (watchdog + drain, then exit) — smoke test.
python -m agents.wake_driver --once

# Custom watchdog / wait-for-wake timeout (seconds).
python -m agents.wake_driver --watchdog-seconds 120

# Preview only — read-only SELECT of recent events through the router,
# no claim, no side effect. Prints a Decision table and exits.
python -m agents.wake_driver --dry-run
python -m agents.wake_driver --dry-run --dry-run-limit 50

# Live routing/escalation, task_queue enqueue + claude -p spawn disabled.
python -m agents.wake_driver --no-task-drain
```

The driver `LISTEN`s on the `events` channel; each `NOTIFY` (fired by the
`notify_events_insert` trigger on `events` insert) wakes a tick. A tick
re-claims stale rows, then drains pending events through the production
orchestrator — `agents.orchestrator.build_production_orchestrator` closes the
pure `handle_event` router over `dispatch`'s side effects (`task_queue`
enqueue on `HANDLE_INLINE`/route-to-task, escalation on `ESCALATE`) — and
spawns `claude -p` workers via `executor.spawn` for the resulting `task_queue`
rows. Ctrl-C stops cleanly.

`SUPABASE_URL` / `SUPABASE_KEY` have no default — the Supabase bridge fails
loudly (`RuntimeError`) if an agent tries to call Supabase without them.

### Staged rollout (#1385 AC-E)

`--dry-run` and `--no-task-drain` are permanent flags, not temporary
scaffolding — a fresh device or a post-incident restart re-validates routing
before re-enabling task drain, same procedure every time:

1. **`--dry-run`** — routes recent events through `handle_event` with no
   claim and no side effect; confirms the routing table looks sane before
   anything touches the queue.
2. **`--no-task-drain`** — runs the live LISTEN/NOTIFY loop with routing and
   escalation active but `task_port=None`. This only suppresses the *spawn*
   half (Step 4's `claude -p` worker launch) — dispatch still enqueues
   `task_queue` rows for `EMIT_TASK` routes in Step 3 regardless of
   `task_port`, since that enqueue happens in `orchestrator.dispatch()`, not
   in the task-drain step.
3. **Default (no flags)** — full loop, task drain and worker spawn enabled.
   This is now the supported steady state: each spawned worker runs isolated
   in its own per-task git worktree (#1390 — see *Worker isolation* below),
   so concurrent workers can no longer race on the same working tree. Spawn
   concurrency is capped by `executor.spawn`'s `DEFAULT_CONCURRENCY_CAP`,
   configurable via `REACTIVE_CONCURRENCY_CAP` (default 5;
   `register-wake-driver.ps1` pins the unattended daemon to 2).

### Worker isolation (#1390)

Each spawned `claude -p` worker gets its own git worktree at
`.reactive/worktrees/<task_id>` (`.reactive/` is gitignored), created by
`task_dispatch._create_task_worktree` before `executor.spawn` and passed
through as the child process's `cwd`. Concurrent workers write to separate
working trees, so their edits/commits never collide, and none of the
isolation is visible from the repo root.

By default the worktree is created on a fresh branch `task/<task_id>`. If
the dispatched goal carries an explicit `(branch=<name>)` directive naming a
*different* branch, `_create_task_worktree` instead attaches to that
existing branch rather than creating `task/<task_id>` — this is how a
fresh-shape retry lands back on the root attempt's branch:
`orchestrator._redrive_goal` pins the retry to `(branch=task/<root_task_id>)`
precisely because that branch already exists (freed by the root attempt's
failed-detach, below) and must be reused, not diverged from (PR #1450
review, MEDIUM).

At the terminal boundary, `task_dispatch._finalize_task_worktree` removes
the worktree outright on success. On failure it detaches HEAD instead of
deleting — this frees the `task/<task_id>` branch ref so a retry can attach
it in a fresh worktree, while the failed worktree itself is retained on disk
for post-mortem inspection.

Every tick, `task_dispatch.sweep_task_worktrees` reaps `.reactive/worktrees/`
before draining events: absent or terminal-success worktrees are pruned
immediately; retained failures are kept up to a TTL and a count cap (oldest
evicted first beyond the cap) so a string of failures can't accumulate
disk forever. The sweep finishes with a best-effort `git worktree prune` and
logs-and-continues on any single removal failure rather than aborting the
tick.

Spawned workers also run under a narrowed `--allowedTools` list —
`Bash(git:*)` was replaced with an explicit subcommand allowlist (`status`,
`diff`, `log`, `show`, `rev-parse`, `checkout`, `fetch`, `add`, `commit`,
`push`); repo-global subcommands that would reach across worktrees
(`git gc`, `git config`, `git worktree`, `git branch -D`, `git reset --hard`)
are unreachable. `checkout`/`fetch` *are* reachable (PR #1450 review,
MEDIUM) — a rework-shape goal (`/rework #N`) never carries a
`(branch=...)` directive, so its worker starts on a fresh `task/<task_id>`
branch with no path to the PR under rework unless it can check out and
fetch the PR's branch itself. Both are safe to expose: `checkout` is
worktree-local (git refuses to check out a branch already checked out in a
sibling worktree) and `fetch` only ever updates remote-tracking refs, never
local branches.

## Production deploy / teardown

`scripts/install/register-wake-driver.ps1` registers a Windows Task Scheduler
entry that runs `python -m agents.wake_driver` (the long-running loop, not
`--once`) as a supervised daemon: starts at logon, restarts on crash, and
sets `JARVIS_PRINCIPAL=autonomous` on the launched process — see
[../security/agent-boundaries.md](../security/agent-boundaries.md). It is a
thin restart wrapper, not a resident poller — the loop itself stays
event-driven via `LISTEN/NOTIFY`.

```powershell
# Register (idempotent — safe to re-run after a script update).
scripts/install/register-wake-driver.ps1

# Inspect the plan without registering anything.
scripts/install/register-wake-driver.ps1 -WhatIfOnly

# Custom watchdog / wait-for-wake timeout (seconds), default 300.
scripts/install/register-wake-driver.ps1 -WatchdogSeconds 120
```

The script is device-guarded to the designated routine host
(`config/device.json`'s `name` vs `$env:JARVIS_SCHEDULED_HOST`) — the
single-driver invariant in `agents/pid_sidecar.py` assumes exactly one
supervised instance, and the routine host is the production target for
always-on agents. Pass `-Force` to register on another device for dev
rehearsal. No device-specific paths/IPs/usernames are hardcoded in the
script itself — `RepoRoot` and the Python interpreter are resolved from the
local machine at registration time.

The earlier NSSM `jarvis-scheduler` resident service was retired in #743 (the
loop is event-driven, not a resident poller). If a device still has that service
registered, remove it cleanly:

```powershell
scripts/install/uninstall-scheduler-service.ps1
```

## Architecture notes

### Why a direct-Postgres socket, not the Supabase client?

The wake signal is Postgres `LISTEN/NOTIFY`. PostgREST (what `supabase-py`
speaks) cannot hold a `LISTEN` — it's stateless HTTP. So wake_driver opens one
direct `psycopg` session connection for the wake channel, while everything else
(reads, task rows, audit) rides `supabase-py`. That split is why
`AGENTS_POSTGRES_URL` and `SUPABASE_URL` are both required and point at the same
project through different endpoints.

### Why a separate Supabase bridge (not MCP)?

MCP is Claude Code's protocol; it isn't available outside a Claude session.
`agents/supabase_client.py` is a thin wrapper over `supabase-py` exposing the
subset of reads/writes agents need. Both sides hit the same tables, so what an
agent writes shows up in Claude Code's `memory_recall` / `events_list` /
`goal_list` and vice versa. Agent writes to `audit_log` set `agent_id`; MCP
writes leave it NULL — the column doubles as the actor differentiator.

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `RuntimeError: AGENTS_POSTGRES_URL is not set` | Set the direct-Postgres session DSN in `.env` — see *Configure environment*. |
| Driver wakes but never spawns a task | NOTIFY triggers missing from the target DB — apply `supabase/migrations/` (`supabase db reset` locally). |
| `LISTEN` returns no notifications | DSN points at the transaction pooler `:6543` — switch to a session `:5432` endpoint. |
| Supabase bridge `RuntimeError` on startup | `SUPABASE_URL` / `SUPABASE_KEY` unset — the bridge fails loud by design. |
