# Dispatcher escalation triggers

Module: `agents/escalation.py`. Ships as **S2-4** (issue #299) — the
guardrails dispatcher (S2-3, #298) calls before every live dispatch. If
any trigger fires, the `task_queue` row flips to `escalated` and an
`events` row (`severity=high`) tells the principal.

## Triggers

| Trigger | Fires when | Context payload |
|---------|------------|-----------------|
| `stale_approval` | `now - approved_at > 7 days` | `approved_at`, `age_days`, `max_age_days` |
| `scope_drift` | `current_scope_hash != approved_scope_hash` | both hashes (or error if hasher raised) |
| `limit_near_exhaustion` | usage probe reports `near_exhaustion=True` | `used`, `total`, `headroom_ratio` |
| `cross_task_conflict` | another `dispatched` row overlaps our `scope_files` | `conflicting_task_id`, `overlapping_files` |
| `pattern_repeat` | same `goal` appears >3 times in the most-recent dispatches | `goal`, `recent_matching_dispatches`, `threshold` |

Thresholds are module-level constants (`STALE_APPROVAL_MAX_DAYS`,
`PATTERN_REPEAT_THRESHOLD`) — change the constant, change the rule
everywhere.

## Design

Pure checks (no DB writes) + one DB-writing `escalate()`:

```python
from agents.escalation import check_all, escalate, EscalationContext

ctx = EscalationContext(
    current_scope_hash=hash_current_files,         # callable or precomputed str
    usage_reading=usage_probe.read_usage(),
    active_dispatched_rows=other_dispatched,       # excludes `row`
    recent_successful_dispatches=recent_newest_first,
)

check = check_all(row, ctx)
if check.should_escalate:
    escalate(row, check)           # writes events + flips task_queue.status
    return                         # dispatcher skips this tick's dispatch
```

`check_all` runs triggers in this priority order:

1. **Stale approval** — principal needs to re-approve, cheapest fix.
2. **Scope drift** — files changed, approval is stale in a different way.
3. **Limit near-exhaustion** — wait for budget, no config change needed.
4. **Cross-task conflict** — another task is already touching these files.
5. **Pattern repeat** — probable loop; principal should inspect the queue.

First-match wins — surfacing five simultaneous reasons would drown the
principal in noise. If multiple fire, the earliest-fixable one shows up in
the event.

## `escalate()` side effect

Two writes, best-effort:

1. Insert into `events`:
   - `event_type = "dispatcher_escalation"`
   - `severity = "high"`
   - `source = "task-dispatcher"`
   - `payload = {queue_id, trigger, context}`
   - `title = "Dispatcher escalated task <id>: <trigger>"`

2. Update `task_queue`:
   - `status = "escalated"`
   - `escalated_reason = "<trigger>: <context dict>"`

A row without an `id` (only really happens in hand-built test rows) still
emits the event; the queue update is skipped rather than failing.

## Testing

`tests/test_agents_escalation.py` — 33 unit tests. Pure checks use
dict rows + fake clock; `escalate()` uses a stub Supabase client that
records insert/update shape.

No live DB required.

## Failure modes

| Failure | Outcome |
|---------|---------|
| Bad timestamp in `approved_at` | Check returns `no_action` (no evidence to escalate from) |
| Hasher callable raises | Treat as drift and escalate (evidence points to "something wrong with scope") |
| `approved_scope_hash` empty | No baseline to drift from → `no_action` |
| `escalate()` called on `EscalationCheck.no_action()` | `ValueError` — catch misuse early |

## Wired-up dependencies

- `agents.usage_probe.UsageReading` — `check_limit_near_exhaustion` consumes.
- `agents.supabase_client.get_client` — `escalate()` uses when `client` is not injected.
- `task_queue` schema (S2-1) — the row shape and the `status`/`escalated_reason` columns.
- `events` table (Sprint 1 allowlist) — dispatcher is authorized to insert.
