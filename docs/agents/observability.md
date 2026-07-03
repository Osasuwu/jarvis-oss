# Observability — first-week checklist

Purpose: when the dispatcher runs unattended (the routine host, NSSM service installed via [#368](https://github.com/Osasuwu/jarvis/pull/382)), we need to answer in under a minute: *did it work overnight, did it spam, did it silently break?*

**Phase 1 = readable text.** No Grafana, no Prometheus. Supabase queries + a thin Python script + Windows event log.

## What to watch

### 1. `audit_log` — primary signal

Every dispatcher tick (success or failure) writes one row. Schema (from `agents/supabase_client.py::audit`):

| Column | Meaning |
|---|---|
| `agent_id` | `task-dispatcher` for the dispatcher; other agents have their own ids |
| `tool_name` | What was invoked (e.g. `claude`, `safety_gate`, `dispatch`) |
| `action` | Specific verb (`spawn`, `tick`, `check`) |
| `target` | Issue/task being handled (when applicable) |
| `details` | JSONB — full context |
| `outcome` | `success` or `failure:<ExceptionType>` |
| `timestamp` | UTC, when row was written |

**Queries (Supabase SQL editor or via `mcp__supabase__execute_sql`):**

Last 24 h activity per agent:
```sql
SELECT agent_id, count(*) AS rows, min(timestamp) AS first, max(timestamp) AS last
FROM audit_log
WHERE timestamp > now() - interval '24 hours'
GROUP BY agent_id
ORDER BY last DESC;
```

Failures in last 24 h:
```sql
SELECT timestamp, agent_id, action, target, outcome, details
FROM audit_log
WHERE timestamp > now() - interval '24 hours'
  AND outcome NOT LIKE 'success%'
ORDER BY timestamp DESC;
```

Gap detection — where did the heartbeat stop:
```sql
WITH lagged AS (
  SELECT timestamp,
         lag(timestamp) OVER (ORDER BY timestamp) AS prev_timestamp
  FROM audit_log
  WHERE agent_id = 'task-dispatcher'
    AND timestamp > now() - interval '7 days'
)
SELECT timestamp,
       extract(epoch FROM (timestamp - prev_timestamp))/60 AS gap_minutes
FROM lagged
WHERE timestamp - prev_timestamp > interval '5 minutes'
ORDER BY gap_minutes DESC
LIMIT 10;
```

### 2. `apscheduler_jobs` — what the scheduler thinks it should run

Persisted job rows. After [#383](https://github.com/Osasuwu/jarvis/pull/383) the startup reaper deletes orphans automatically — if you see unexpected ids, something registered them post-startup.

```sql
SELECT id, next_run_time
FROM apscheduler_jobs
ORDER BY next_run_time;
```

Expected rows in production: `task-dispatcher` only. With `--placeholder` flag also `_pickle_canary_tick`. Anything else = red flag.

### 3. NSSM service state on the routine host

```powershell
Get-Service jarvis-scheduler
# Status should be Running, StartType Automatic
```

Service stdout / stderr (live tail):
```powershell
Get-Content "C:\path\to\jarvis\logs\scheduler\stdout.log" -Tail 50 -Wait
Get-Content "C:\path\to\jarvis\logs\scheduler\stderr.log" -Tail 50 -Wait
```

Windows event log for service crashes / restarts:
```powershell
Get-EventLog -LogName System -Source "Service Control Manager" -Newest 20 |
  Where-Object { $_.Message -like "*jarvis*" }
```

### 4. Telegram channels — escalations + dispatch reports

Principal-facing Telegram channel receives:
- Escalations from the escalation ladder ([#327](https://github.com/Osasuwu/jarvis/issues/327)) — principal action needed
- Dispatcher post-run reports (success/failure summaries)

Silence in Telegram is **not** evidence of health — could mean dispatcher is down. Cross-reference with `audit_log`.

## Morning check — one shot

Run on any host that has Supabase MCP credentials:

```bash
python scripts/observability/morning_check.py
```

Outputs a text report: 24 h dispatch count per agent, failures, gaps, stale orphans. If it prints nothing alarming and the audit_log row count matches expected ticks (≈ `86400 / interval_seconds`), things are fine.

## Threshold rules — when does silence mean broken

| Signal | Healthy | Concerning | Broken |
|---|---|---|---|
| Gap in `audit_log` for `task-dispatcher` | < 2× `--interval` | 2-4× | > 4× or > 2 hours |
| Failure rate (24 h) | < 5 % of ticks | 5-25 % | > 25 % |
| `apscheduler_jobs` rows | matches registered set | unexpected id present | 0 rows on running service |
| `Get-Service jarvis-scheduler` | Running | Stopped (service start pending) | Not found / Disabled |

`> 2 hours` of silence on `task-dispatcher` = wake principal via Telegram (when escalation auto-pinging is wired) or page manually.

## What this doc is NOT

- Not real-time alerting — Phase 1 is reactive (principal queries on demand)
- Not a dashboard — principal runs the morning check at their own cadence
- Not exhaustive metrics — captures liveness + failure rate, nothing finer-grained

If/when we want auto-alerts or dashboards, file a separate issue. This is the minimum viable observability.
