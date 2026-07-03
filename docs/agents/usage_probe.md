# Claude Max usage probe

Module: `agents/usage_probe.py`. Ships as **S2-2** (issue #297) — the
lightweight budget gate the task-dispatcher (S2-3, #298) consults before
every live dispatch.

## Why a probe, not a live API

Claude Max has no public per-user quota endpoint. The dispatcher still
needs a "do I have headroom?" answer every tick. The probe gives one
authoritative answer: **count the dispatcher's own successful dispatches
in a rolling window**. That's exactly the budget this jurisdiction
consumes, and we already write one audit row per dispatch (S2-0 safety
gate). If Anthropic exposes a real API later, swap the probe
implementation — dispatcher only depends on the `UsageProbe` Protocol.

## Usage

```python
from agents.usage_probe import read_usage

reading = read_usage()        # builds default StaticBudgetProbe + cache
if reading.near_exhaustion:
    return  # dispatcher pauses this tick
```

`read_usage` **never raises**. A probe failure is logged and replaced with
a reading that has `near_exhaustion=True` — conservative false-safe, per
the issue's acceptance criteria.

## Configuration

All knobs are env vars:

| Env var | Default | Meaning |
|---------|---------|---------|
| `CLAUDE_USAGE_WINDOW_HOURS` | `5` | Rolling window matching Claude Max's 5-hour quota |
| `CLAUDE_USAGE_BUDGET` | `100` | Dispatches allowed per window (tune as we learn) |
| `CLAUDE_USAGE_NEAR_EXHAUSTION_PERCENT` | `15` | Flip `near_exhaustion` when ≤ this much headroom remains |
| `CLAUDE_USAGE_CACHE_TTL_SECONDS` | `300` | In-process cache TTL (re-probe at most once per 5 min) |

Bad values (non-int) fall back to the default with a warning log — never
a crash.

## Cache semantics

`CachedProbe` memoizes the reading in process memory for the TTL. The
dispatcher runs as one long-lived APScheduler process, so cross-process
cache (Supabase) would add complexity without benefit:

- First tick after process start → fresh probe.
- Subsequent ticks within TTL → cached reading.
- TTL expiry → re-probe.
- `probe.invalidate()` → force next read (dispatcher can call this after
  a confirmed dispatch that materially shifts headroom).

A restart simply re-probes — no stale cache leaks across process lifetimes.

## Reading shape

```python
@dataclass(frozen=True)
class UsageReading:
    limit_window: timedelta   # e.g. 5h
    used: int                 # successful dispatches since window_start
    total: int                # CLAUDE_USAGE_BUDGET at probe time
    reset_at: datetime        # upper bound on next refill
    near_exhaustion: bool     # dispatcher gate
```

`reset_at = now + limit_window` because Claude Max windows aren't aligned
to a public clock pivot. Dispatcher only gates on `near_exhaustion`; the
other fields are for dashboards and audit.

## Source data

`StaticBudgetProbe` counts rows in `audit_log` where:

- `agent_id = 'task-dispatcher'`
- `action = 'dispatch'`
- `outcome = 'success'`
- `created_at >= now - window`

The count is capped at `total` — an over-count (shouldn't happen, but be
resilient) still produces a sane reading with `near_exhaustion=True`.

## Failure modes

| Failure | Outcome |
|---------|---------|
| Supabase unreachable | `UsageProbeError` → `read_usage` returns false-safe reading (`near_exhaustion=True`, `total=0`) |
| Env var is a typo (e.g. `"abc"`) | Warning logged, default used |
| `supabase=None` (no client wired) | `used=0` returned — tests exercise headroom math without a live DB |

## Smoke test

```python
from unittest.mock import MagicMock
from agents.usage_probe import StaticBudgetProbe

mock = MagicMock()
mock.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.gte.return_value.execute.return_value.count = 90

probe = StaticBudgetProbe(supabase=mock, total=100, near_exhaustion_percent=15)
reading = probe.read()
assert reading.used == 90
assert reading.near_exhaustion is True  # 10% headroom <= 15% threshold
```

Full unit suite (`tests/test_agents_usage_probe.py`) uses a hand-rolled
stub client and fake clock — no live DB or real time passing required.
