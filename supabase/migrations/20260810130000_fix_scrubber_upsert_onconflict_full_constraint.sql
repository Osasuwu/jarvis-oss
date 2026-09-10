-- #1498: scrubber_block_event_upsert / scrubber_disabled_event_upsert both
-- still restate the old partial-index predicate in their ON CONFLICT target,
-- which matched events.dedup_key's old PARTIAL unique index. Migration
-- 20260810120000_events_dedup_key_full_unique_constraint converted that
-- column to a FULL unique constraint (events_dedup_key_key) to fix #953/#1491
-- for supabase-py's bare upsert callers — but a WHERE-qualified conflict
-- target only matches a partial index, never a full constraint (the inverse
-- of the original bug), so both RPCs have 42P10'd on every call since prod
-- applied that migration. write_scrubber.py's fail-open try/except swallows
-- the error, so no mcp_write_scrubber_block / mcp_write_scrubber_disabled
-- event has landed since. Dropping the WHERE clause restores a plain
-- conflict target matching the full constraint — same fix shape as #1491,
-- applied here instead of reverted.
create or replace function scrubber_block_event_upsert(
  p_dedup_key   text,
  p_severity    text,
  p_repo        text,
  p_write_path  text,
  p_patterns    jsonb,
  p_seen_at     timestamptz default now()
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
    'mcp_write_scrubber_block',
    p_severity,
    p_repo,
    'mcp_memory',
    'Write blocked by secret scrubber (' || p_write_path || ')',
    jsonb_build_object(
      'write_path', p_write_path,
      'patterns', p_patterns,
      'occurrence_count', 1
    ),
    p_dedup_key,
    p_seen_at
  )
  on conflict (dedup_key) do update
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
  on conflict (dedup_key) do update
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
