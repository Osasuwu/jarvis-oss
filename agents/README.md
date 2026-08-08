# agents/

Federation & Delegation pillar — the **reactive-core** loop (milestone #44).
The loop wakes on Postgres `LISTEN/NOTIFY`, routes each event through a
deterministic orchestrator, and spawns `claude -p` workers for coding tasks.
Agents run alongside Claude Code (not as a replacement) and share state with it
through Supabase (events, `task_queue`, memories, goals, `audit_log`).

See `docs/agents/` for per-module operational notes and architectural detail.

| Module | Doc | Role |
|--------|-----|------|
| `config.py` | [`reactive-core-setup.md`](../docs/agents/reactive-core-setup.md) | `load_config()` — `AGENTS_POSTGRES_URL` + Supabase URL/key from env |
| `wake_driver.py` | [`reactive-core-setup.md`](../docs/agents/reactive-core-setup.md) | Cold-boot `LISTEN/NOTIFY` driver — the reactive-core run loop (#741) |
| `orchestrator.py` | — | Deterministic event router (#744) — `(event_type, severity)` → inline / task / escalate |
| `task_queue.py` | — | `task_queue` FSM interface (#740) — enqueue / claim_next / transition |
| `task_dispatch.py` | — | Closes the reactive forward path (#909) — event → task → spawn → poll |
| `poller.py` | — | Parked-event re-queue poller — Path B (#745) |
| `executor.py` | — | Fire-and-forget `claude -p` spawn (salvaged from the retired dispatcher) |
| `escalation.py` | [`escalation.md`](../docs/agents/escalation.md) | First-match triggers (S2-4) — route a row to owner attention |
| `safety.py` | [`safety.md`](../docs/agents/safety.md) | Identity + tier classifier + audit log (S2-0) |
| `usage_probe.py` | [`usage_probe.md`](../docs/agents/usage_probe.md) | 5h Claude Max budget probe (S2-2) |
| `supabase_client.py` | — | Supabase bridge — `get_client()` + `audit()` + event/task reads & writes |
| `ollama_client.py` | [`ollama-setup.md`](../docs/agents/ollama-setup.md) | Local LLM client — **staged-dormant**, no live consumer yet |
| `github_client.py` | — | Thin wrapper over the GitHub Events API |
| `scope_hash.py` | — | Deterministic scope-files hash — drift detection (#773) |
| `pid_sidecar.py` | — | PID sidecar for restart-surviving liveness (#952) |

## Running the loop

Requires `AGENTS_POSTGRES_URL` + `SUPABASE_*` env vars set (loaded from `.env`)::

    python -m agents.wake_driver                    # long-running: LISTEN, drain, watchdog
    python -m agents.wake_driver --once             # single tick (watchdog + drain), then exit
    python -m agents.wake_driver --watchdog-seconds 120

See [`docs/agents/reactive-core-setup.md`](../docs/agents/reactive-core-setup.md)
for environment setup and the local-Supabase walkthrough.

## Running tests

Unit tests (no live services — run on every CI commit)::

    pytest tests/test_agents_*.py -x -q

The agent unit suite is hermetic: the Supabase bridge and subprocess spawn are
mocked, so no live infrastructure is required. Tests that touch real Supabase
tag every row with a UUID marker embedded in `goal` / `target` / `title` and
sweep them in teardown, so concurrent runs (two dev machines, CI + local) don't
collide.

## Environment

Required env vars (loaded from `.env`)::

    AGENTS_POSTGRES_URL=postgresql://postgres:[YOUR-PASSWORD]@db.<ref>.supabase.co:5432/postgres
    SUPABASE_URL=https://<project>.supabase.co
    SUPABASE_KEY=<service-role-or-anon>

Optional::

    OLLAMA_HOST=http://localhost:11434   # staged-dormant local inference
    OLLAMA_MODEL=qwen3:4b
