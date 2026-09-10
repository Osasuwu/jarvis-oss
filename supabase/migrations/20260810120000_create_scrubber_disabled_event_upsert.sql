-- scrubber_disabled_event_upsert: day-bucketed dedup + occurrence counter for
-- mcp_write_scrubber_disabled events (AC1, #1000 — code-review round-2 fix).
-- Mirrors scrubber_block_event_upsert (20260809180000) exactly: a plain
-- `.table("events").upsert(..., on_conflict="dedup_key")` cannot satisfy
-- events.dedup_key's PARTIAL unique index (idx_events_dedup_key ... where
-- dedup_key is not null) — Postgres only infers a partial unique index as the
-- ON CONFLICT arbiter when the predicate is restated in the conflict target,
-- so the original plain-upsert implementation silently failed every write
-- (swallowed by write_scrubber.py's broad except-and-log) and no disabled-gate
-- event ever landed.
create or replace function scrubber_disabled_event_upsert(
  p_dedup_key  text,
  p_reason     text,
  p_repo       text,
  p_seen_at    timestamptz default now()
) returns events
language plpgsql
security invoker
set search_path = public
as $$
declare
  result events;
begin
  insert into events as e (
    event_type, severity, repo, source, title, payload, dedup_key, event_at
  )
  values (
    'mcp_write_scrubber_disabled',
    'high',
    p_repo,
    'mcp_memory',
    'Tier-2 write-scrubber gate is disabled',
    jsonb_build_object(
      'reason', p_reason,
      'occurrence_count', 1
    ),
    p_dedup_key,
    p_seen_at
  )
  on conflict (dedup_key) where dedup_key is not null do update
    set payload = jsonb_set(
          e.payload,
          '{occurrence_count}',
          to_jsonb(coalesce((e.payload->>'occurrence_count')::int, 1) + 1)
        ),
        event_at = excluded.event_at
  returning e.* into result;
  return result;
end;
$$;
