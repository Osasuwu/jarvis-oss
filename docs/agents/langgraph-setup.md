# LangGraph Agents — Dev Setup

Federation & Delegation pillar — persistent agents run alongside Claude Code. LangGraph handles the lifecycle (graph definition, state, checkpointing), Ollama handles inference, local Postgres stores checkpoints.

This document covers the Sprint 1 foundation (issue #172). Agent implementations live in later sprints.

## Prerequisites

- Python 3.11+ (project requirement)
- Ollama running locally — see [ollama-setup.md](ollama-setup.md)
- Docker Desktop (for local Postgres)

## Install dependencies

From repo root:

```bash
pip install -e ".[agents]"
```

This pulls:

| Package | Purpose |
|---------|---------|
| `langgraph` | Graph runtime + state machine |
| `langchain-ollama` | LangChain adapter (reserved for future graph nodes) |
| `langgraph-checkpoint-postgres` | Postgres checkpointer backend |
| `psycopg[binary,pool]` | Postgres driver + connection pool |
| `ollama` | Official Python client (used directly for chat with `think=False`) |
| `supabase` | Agent bridge to the shared knowledge base (memories, events, goals, audit_log) |

## Start local Postgres

```bash
docker compose -f docker-compose.agents.yml up -d
docker compose -f docker-compose.agents.yml ps   # expect 'healthy'
```

Exposes Postgres on `localhost:5433` (port 5432 left free for any system-wide Postgres).

Credentials in the compose file are dev-only:
- user `jarvis`, password `jarvis`, database `agents`
- data lives in named volume `agents_postgres_data`

To stop without losing checkpoints: `docker compose -f docker-compose.agents.yml stop`.
To wipe state: `docker compose -f docker-compose.agents.yml down -v`.

**Production deploy note:** The NSSM scheduler service this section described
was retired in #743 — the reactive-core loop (`agents/wake_driver.py`) is
event-driven and runs foreground for now. See [scheduler.md](scheduler.md) for
the retirement details and per-device teardown.

## Configure environment

Copy the relevant lines from `.env.example` into `.env`:

```
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
AGENTS_POSTGRES_URL=postgresql://jarvis:jarvis@localhost:5433/agents?sslmode=disable

# Supabase bridge — same vars the MCP memory server uses; re-used here.
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<anon-or-service-key>
```

Defaults in `agents/config.py` match the Ollama and Postgres values, so `.env` is optional for local inference/checkpointing. `SUPABASE_URL` / `SUPABASE_KEY` have no default — the bridge fails loudly (`RuntimeError`) if an agent tries to call Supabase without them configured.

## Run the minimal graph

```bash
# First run: creates checkpoint tables, executes the graph, prints the reply.
python -m agents.main

# Second run in a fresh process: loads the saved state.
python -m agents.main --resume

# Custom thread ID:
python -m agents.main --thread smoke-2
```

Expected output (first run):

```
[run]  thread=demo-thread
[run]  reply:         ok.
[run]  step:          1
[run]  checkpoint_id: 1ef4f797-8335-6428-8001-8a1503f9b875
```

`--resume` in a brand-new Python process should print the same `reply`, `step`, and `checkpoint_id` — that is what validates "survives restart".

## Run the event monitor (issue #174)

The first real agent. Polls GitHub Events, classifies via Ollama, writes non-noise events to Supabase.

```bash
# Default: watch Osasuwu/jarvis, thread "event-monitor"
python -m agents.event_monitor

# Custom repo and thread
python -m agents.event_monitor --repo Osasuwu/jarvis --thread smoke
```

Expected output:

```
[monitor] thread=event-monitor
[monitor] repos:   Osasuwu/jarvis
[monitor] fetched: 3
[monitor] stored:  2
[monitor] cursors: {'Osasuwu/jarvis': '<latest-event-id>'}
```

Restart-safety check: run the command twice in a row. The second run should show `fetched: 0` unless new activity landed in between — cursors persist in the Postgres checkpoint.

Optional env for higher rate limits:

```
GITHUB_TOKEN=ghp_...   # unauthenticated = 60 req/hour per IP
```

The agent identifies itself in `events.source` and `audit_log.agent_id` as `langgraph-monitor`.

For the full end-to-end validation (automated suite + manual walkthrough), see [e2e-test.md](e2e-test.md).

## Architecture notes

### Why both `ollama` and `langchain-ollama`?

`ollama` (the official Python client) has first-class support for the `think` parameter needed to disable Qwen3 reasoning. At the time of writing `langchain-ollama`'s `ChatOllama` does not expose `think` cleanly. The minimal graph therefore calls `ollama.Client.chat(..., think=False)` directly through `agents/ollama_client.py`.

`langchain-ollama` stays as a dependency so future graph nodes that need LangChain tool-calling / structured output primitives can adopt it without a new install step.

### Why local Postgres, not Supabase?

See memory `self_hosted_postgres_future_plan`. Sprint 1 uses local Docker Postgres to keep scope tight; migration to a self-hosted Postgres on the routine host is a separate infra track scheduled for when the home LAN is in place.

### Why port 5433?

Port 5432 is the default for system Postgres installs. Running the dev container on 5433 avoids collisions and lets the two coexist.

### Why a separate Supabase bridge (not MCP)?

MCP is Claude Code's protocol; it isn't available inside LangGraph nodes. `agents/supabase_client.py` is a thin wrapper over `supabase-py` that exposes the subset of reads/writes agents actually need (`list_memories`, `list_events`, `list_goals`, `store_event`, `mark_event_processed`, `update_goal_progress`, `audit`). Both sides hit the same tables, so what an agent writes shows up in Claude Code's `memory_recall` / `events_list` / `goal_list` and vice versa.

Agent writes to `audit_log` set `agent_id` (e.g. `"langgraph-monitor"`); MCP writes leave it NULL — the column doubles as the actor differentiator.

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `connection refused` on invoke | Docker not up — `docker compose -f docker-compose.agents.yml up -d` |
| `TypeError: tuple indices must be integers` from checkpointer | psycopg connection not opened via `PostgresSaver.from_conn_string`. Always use the context manager — `main.py` does this already. |
| `message.content` is empty | `think=False` was dropped — see `ollama-setup.md`. The shared wrapper defaults to `False`; override only when you want the reasoning trace. |
| `No prior checkpoint for thread 'X'` on `--resume` | Run without `--resume` first, then `--resume`. Different `--thread` values are independent. |
