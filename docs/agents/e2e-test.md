# Federation & Delegation End-to-End Test (issue #175)

Validates the full event pipeline:

```
GitHub event -> event_monitor (fetch -> classify -> store)
              -> Supabase events + audit_log
              -> Claude Code reads via MCP events_list
```

Two layers:

1. **Automated** (`tests/test_agents_integration.py`) — opt-in pytest
   suite. Injects synthetic events so the pass/fail signal is
   deterministic; everything downstream of fetch (Ollama, Supabase,
   Postgres checkpointer) runs for real.
2. **Manual walkthrough** — you create an actual GitHub issue or PR and
   watch it flow through the system. Slower, but exercises the parts the
   automated suite mocks (real GitHub API, real event shape).

Run both when changing anything in `agents/event_monitor.py`,
`agents/supabase_client.py`, or the `events` / `audit_log` schemas.

## Prerequisites

Same as the setup doc, all running locally:

| Service | Check |
|---------|-------|
| Docker Postgres | `docker compose -f docker-compose.agents.yml ps` shows `healthy` |
| Ollama | `curl -s http://localhost:11434/api/tags` lists `qwen3:4b` |
| Supabase credentials | `SUPABASE_URL` + `SUPABASE_KEY` set in `.env` |
| GitHub (manual only) | Push access to a test repo, optional `GITHUB_TOKEN` |

## Automated suite

Opt-in via an env var — CI doesn't have live Ollama / Supabase /
Postgres, so the default is skip.

```bash
AGENTS_E2E=1 pytest tests/test_agents_integration.py -v
```

Three tests:

| Test | Asserts |
|------|---------|
| `test_full_pipeline_injects_and_stores` | A synthetic `IssuesEvent` flows through classify + store and reappears via `supabase_client.list_events` with `source=langgraph-monitor` and a payload containing the original event id. |
| `test_restart_skips_via_cursor` | Invoking the graph twice with the same thread id does **not** create duplicate rows — the checkpointed cursor filters out already-processed events. |
| `test_audit_log_records_poll` | Every poll (including empty ones) leaves an `audit_log` row with `agent_id=langgraph-monitor`, `tool_name=event_monitor`, `action=poll`. |

Each test embeds a UUID marker in both the event title and the repo
name, so teardown can delete its rows from `events` (match on
`title LIKE '%<marker>%'`) and `audit_log` (match on
`target LIKE '%<marker>%'`). Thread ids are also UUID-suffixed so
parallel invocations (and the production `event-monitor` thread) never
share checkpoint state.

Expected runtime: about one minute — most of it is Ollama classification.

## Manual walkthrough

Use when you want to verify the real GitHub API path, not just the
classify/store chain.

1. **Pick a fresh thread id** so you're not reading through a polluted
   checkpoint history:

   ```bash
   python -m agents.event_monitor --thread manual-$(date +%s)
   ```

   First run prints `fetched: N, stored: M`. Stored should be ≤ N
   (the delta is events classified as noise).

2. **Trigger a new event.** Open an issue on `Osasuwu/jarvis` — e.g.:

   ```bash
   gh issue create --repo Osasuwu/jarvis \
     --title "e2e test $(date -Iseconds)" \
     --body "Smoke test — safe to close."
   ```

3. **Re-run the monitor with the same thread id.**

   ```bash
   python -m agents.event_monitor --thread <the id from step 1>
   ```

   Expected: `fetched: 1+` (your new issue plus any other activity),
   `stored: 1+`. Cursor advances past the new event's id.

4. **Verify Claude Code sees it.** In any Claude Code session:

   ```
   events_list repo=Osasuwu/jarvis
   ```

   Your event should appear with `Source: langgraph-monitor`, severity
   `info` or `medium`, and the payload's `github_event_id` matching the
   real event id GitHub assigned.

5. **Verify audit trail.** Query Supabase:

   ```sql
   SELECT timestamp, agent_id, action, target, details
   FROM audit_log
   WHERE agent_id = 'langgraph-monitor'
   ORDER BY timestamp DESC
   LIMIT 5;
   ```

   Expect one row per monitor invocation, including the empty polls.

6. **Confirm restart safety.** Run the monitor again **without any new
   activity**:

   ```bash
   python -m agents.event_monitor --thread <the same id>
   ```

   Expected: `fetched: 0, stored: 0`. Cursor unchanged. This is the
   acceptance criterion from #174/#175 — no duplicate processing.

7. **Clean up.** Close the test issue. Optionally remove the synthetic
   events:

   ```sql
   DELETE FROM events WHERE source = 'langgraph-monitor'
     AND title LIKE '%e2e test%';
   ```

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `postgrest.APIError: column audit_log.created_at does not exist` | Code is reading the wrong column. `audit_log` uses `timestamp`, not `created_at`. See `mcp-memory/server.py` for the canonical schema. |
| Test fixture fails to clean up | The marker is logged in the assertion message; drop manually with `DELETE FROM events WHERE title LIKE '%<marker>%'`. |
| `fetched: 0` on run 1 with a new thread | The test repo genuinely has no events in the allow-list. Trigger any event type from `RELEVANT_EVENT_TYPES` (issue/PR/push/comment/review). |
| `stored: 0` when `fetched: N>0` | Classifier is calling everything noise. Check the Ollama prompt in `_CLASSIFY_SYSTEM` and the events themselves — automated bumps and trivial bot pushes legitimately classify as noise. |
