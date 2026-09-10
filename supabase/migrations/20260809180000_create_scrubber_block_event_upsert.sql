-- scrubber_block_event_upsert: day-bucketed dedup + occurrence counter for
-- mcp_write_scrubber_block events (AC2, #1000). Mirrors review_debt_upsert's
-- on-conflict-do-update shape (20260721120000_create_review_debt.sql), but
-- events.payload is jsonb with no dedicated counter column, so the increment
-- happens via jsonb_set/coalesce instead of a plain integer column bump.
--
-- events.dedup_key carries only a PARTIAL unique index
-- (idx_events_dedup_key ... where dedup_key is not null), not a table-level
-- unique constraint like review_debt.dedup_key. Postgres only infers a
-- partial unique index as the ON CONFLICT arbiter when the index predicate is
-- restated in the conflict target — hence the explicit
-- `where dedup_key is not null` below. Omitting it raises "there is no
-- unique or exclusion constraint matching the ON CONFLICT specification".
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
