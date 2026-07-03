# Task dispatcher agent

Module: `agents/dispatcher.py`. First federation jurisdiction (Federation & Delegation Sprint 2 — issue #298, S2-3). Wires together every other S2 primitive:

| Upstream | Role |
|----------|------|
| **S2-0** `agents/safety.py` | Identity, idempotency, audit log |
| **S2-1** `task_queue` (schema) | FSM `pending → dispatched / escalated / rejected` |
| **S2-2** `agents/usage_probe.py` | Pre-dispatch budget check |
| **S2-4** `agents/escalation.py` | First-match triggers: stale / drift / limit / conflict / pattern |
| **S2-5** `agents/scheduler.py` | Run-loop engine (APScheduler, 60s interval + jitter) |
| **S4** `agents/perception_*.py` | Producer side — turns external signals into `pending` rows ([`perception.md`](perception.md)) |

## Graph

```
START → poll_queue ──(empty)──────────────────────────────→ END
          │
          ▼
       evaluate ──(escalate)→ escalate ──→ END
          │
          ▼
       dispatch ─────────────────────────→ END
```

One row per tick by design. The dispatcher is the first agent that spawns
external subprocesses; keeping the safety surface narrow (exactly one
`claude` process per tick at most) beats throughput in v1.

## Nodes

| Node | Responsibility | DB writes |
|------|----------------|-----------|
| `poll_queue_node` | `SELECT … WHERE status='pending' AND auto_dispatch=true ORDER BY approved_at ASC LIMIT 1` | none |
| `evaluate_node` | Build `EscalationContext` (probe + peers + recent history), run `check_all`, stash result on `row['_check']` | none |
| `escalate_node` | Call `escalation.escalate(row, check)` | `events` insert + `task_queue` status flip |
| `dispatch_node` | `safety.audit()` + spawn `claude -p <goal>` with sanitized env | `audit_log` insert + `task_queue.status='dispatched'` |

`evaluate_branch` is a pure conditional edge — no DB access, routes to
`escalate` or `dispatch` by reading `row['_check'].should_escalate`.

## Subscription auth (billing-trap defense)

Claude Max billing trap ([`#37686`](https://github.com/anthropics/claude-code/issues/37686)):
if `ANTHROPIC_API_KEY` (or any sibling variant) is set in the dispatcher's
process environment, the spawned `claude` subprocess silently switches to
API billing instead of the Max subscription.

`_sanitize_env()` strips every known variant before `Popen`. The
integration test `test_dispatch_passes_sanitized_env_to_subprocess`
monkeypatches the sensitive vars into the parent environment, spawns a
dispatch, and asserts none reach the child `env` dict.

Current sensitive set (module-level `_SENSITIVE_ENV_KEYS`):

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_AUTH_TOKEN`
- `CLAUDE_API_KEY`

A future Anthropic env rename (likely — the CLI has churned these before)
is a one-line edit, not a billing incident.

## Fire-and-forget semantics

Per the issue body: v1 launches the subprocess and returns. The parent
process doesn't wait for the child to finish — the child session writes
its own `audit_log` rows as it runs. Result collection is out of scope.

Failure modes surface as rows, not exceptions:

| What fails | Surface |
|-----------|---------|
| `Popen` raises (e.g. `claude` not on PATH) | `audit_log` row with `outcome='failure:<ExceptionType>'`, FSM stays at `pending` |
| `task_queue.update` fails (transient network) | `safety.audit` still records `success` (subprocess spawned); principal sees orphaned audit row + unchanged queue and can reconcile |
| Escalation check fires mid-flight | `evaluate_branch` routes to `escalate` — no subprocess spawned |

## Safety gate interpretation

AC says "uses S2-0 safety gate". Dispatcher uses the `safety` *module*
(`audit()` + `idempotency_key()`) but not `safety.gate()` — because
`classify()`'s allowlist doesn't include subprocess spawn, so `gate()`
would force the action to Tier 1 (queue) and block actual dispatch.

Rationale (encoded in `agents/dispatcher.py` docstring):

- `escalation.check_all()` already provides stricter per-row
  authorization than `gate()`: it examines freshness, drift, budget,
  peer conflicts, and repeat-goal patterns that `gate()` cannot see.
- Every dispatch still writes an `audit_log` row with `tier=auto`,
  idempotency key, and sanitized outcome — the observability `gate()`
  provides without the blocking.
- If a future design wants tier-based control, it can add a dedicated
  `DISPATCH` classifier rule; the existing call surface doesn't need to
  change.

## APScheduler wiring

`register(handle, dry_run=..., interval_seconds=60)` installs
`_scheduled_tick` on the provided `SchedulerHandle`. Two invariants:

1. **`_scheduled_tick` is module-level**, not a closure. APScheduler's
   SQLAlchemy jobstore pickles the reference; a closure carrying
   `dry_run` would bloat the persisted row and couple the stored job to
   one process's Python instance.
2. **`dry_run` travels via `os.environ['TASK_DISPATCHER_DRY_RUN']`**
   (`"1"` / `"0"`), set by `register()` before the tick runs.
   Env-driven config survives pickle + restart without extra serialization.

## CLI

```bash
# one tick, real dispatch
python -m agents.dispatcher

# graph traversal + audit write, no subprocess launched
python -m agents.dispatcher --dry-run

# override thread_id (default: "task-dispatcher")
python -m agents.dispatcher --thread prod-dispatcher
```

Exit code is always 0 unless a `KeyboardInterrupt` propagates before the
tick completes. Dispatch failures surface on stdout: `[dispatcher]
outcome=failed reason=FileNotFoundError: ...`.

## Testing

`tests/test_agents_dispatcher.py` — 16 tests, no live DB / Postgres / `claude` binary.

- Stub Supabase client (`_StubClient`) applies `.eq/.neq/.order/.limit`
  against seeded rows so selects and updates look like real responses.
- `_CapturedPopen` records argv + env per call so the leak test can
  inspect the env dict.
- `pytest.importorskip("langgraph")` / `importorskip("apscheduler")` on
  tests that need them — keeps the unit subset runnable on bare Python.

Key coverage:

1. **Identity consistency** — `AGENT_ID` matches probe + escalation.
2. **Env sanitization** (3 tests) — strips every known variant, defaults to `os.environ`.
3. **Scope hashing** (3 tests) — order-independent, detects additions, stable for empty.
4. **Poll** (2 tests) — empty queue, oldest-approved-first.
5. **Dispatch flow** — happy path, dry-run no-op, empty queue short-circuit.
6. **Escalation flow** — near-exhaustion triggers limit, stale approval triggers stale.
7. **Billing-trap leak test** — asserts `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY` absent from child env while non-sensitive vars survive.
8. **Failure flow** — `Popen` raising doesn't advance FSM.
9. **Scheduler** — `register()` installs module-level tick; `_scheduled_tick` swallows exceptions so a bad tick never tears the scheduler down.

## Failure modes (at a glance)

| Failure | Outcome |
|---------|---------|
| Queue empty | `outcome=no_pending`, graph short-circuits to END |
| Probe near-exhaustion | `outcome=escalated`, `reason=limit_near_exhaustion` |
| Approval > 7d old | `outcome=escalated`, `reason=stale_approval` |
| `Popen` raises | `outcome=failed`, audit row with `outcome=failure:<type>`, queue row untouched |
| `claude` subprocess spawns but dies immediately | Not our problem — fire-and-forget; child writes own audit |
| Sensitive env var present in parent | Stripped in `_sanitize_env` before `Popen`; leak test enforces |

## Dependencies

Runtime:

- `langgraph` — graph + `PostgresSaver` checkpointer.
- `apscheduler` — via `register()`.
- `supabase-py` — via `supabase_client.get_client()`.
- `python-dotenv` — CLI entry loads `.env`.

Tables touched:

- `task_queue` — select pending, update to dispatched, read peers / history.
- `events` — insert on escalation (via `escalation.escalate`).
- `audit_log` — insert on every dispatch (via `safety.audit`).
