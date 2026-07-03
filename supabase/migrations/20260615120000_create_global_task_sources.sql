-- Issue #679: Global task sources — non-repo inputs for AFK loop
-- Registry of recurring, low-stakes, repo-agnostic tasks. Advancer ticks due rows
-- into events queue; orchestrator routes via three-disposition logic.

-- Dependency: this migration assumes the `events` table already exists (the
-- advancer INSERTs global_task_due rows into it, deduped on events.dedup_key).
-- `events` is defined by mcp-memory/schema.sql, applied out-of-band — no
-- Supabase migration creates it. Fail fast with a clear message rather than
-- letting the advancer hit a missing-relation error at first tick.
do $$
begin
  if not exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'events'
  ) then
    raise exception
      'global_task_sources requires the events table (advancer emits global_task_due rows there). Apply mcp-memory/schema.sql first.';
  end if;
end $$;

create table if not exists global_task_sources (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  body text,
  dispatcher_skill text not null
    check (dispatcher_skill in ('research', 'self-improve', 'status-record', 'last-work-report')),
  output_sink text not null
    -- 'event_reemit' is RESERVED / NOT-YET-IMPLEMENTED (see #679): the enum value
    -- exists so the dispatcher_sink_compatibility matrix can already forbid the
    -- nonsensical pairings, but no advancer/orchestrator path consumes it yet.
    check (output_sink in ('memory', 'telegram_digest', 'event_reemit')),
  payload jsonb default '{}',
  cadence interval,                 -- NULL = one-shot; >= 1s floor added below
  last_run timestamptz,
  next_run timestamptz,
  enabled bool not null default true,
  on_lapse text not null default 'coalesce'
    check (on_lapse in ('coalesce', 'fire_per_interval')),
  created_at timestamptz not null default now()
);

-- Dispatcher/sink compatibility matrix (enforced at DB level).
-- status-record -> memory only; research -> any sink; self-improve /
-- last-work-report -> any sink EXCEPT event_reemit (re-emitting an event from a
-- self-improve or report run loops the advancer against its own output; neither
-- skill produces an event-shaped result, so the pairing is incoherent).
-- drop-then-add (not the add-only DO/EXCEPTION guard): this matrix was tightened,
-- so a DB carrying the earlier looser constraint must have it replaced — an
-- add-only guard would hit duplicate_object and silently keep the old form.
-- DROP ... IF EXISTS is a no-op on a fresh DB. create table-if-not-exists above
-- no-ops when the table pre-exists, so the constraint ALTERs are what actually
-- carry the change forward to an already-created table.
alter table global_task_sources drop constraint if exists dispatcher_sink_compatibility;
alter table global_task_sources add constraint dispatcher_sink_compatibility
  check (
    (dispatcher_skill = 'status-record' and output_sink = 'memory') or
    (dispatcher_skill = 'research') or
    (dispatcher_skill = 'self-improve' and output_sink <> 'event_reemit') or
    (dispatcher_skill = 'last-work-report' and output_sink <> 'event_reemit')
  );

-- cadence floor of 1 second. The advancer divides by EXTRACT(EPOCH FROM cadence)
-- when counting lapsed intervals, so a zero cadence is a divide-by-zero that
-- aborts the whole advance transaction; a sub-second cadence makes the
-- int(epoch) dedup_key collide across ticks. NULL stays a valid one-shot. Added
-- as a separate named ALTER (not inline on the column) so it lands on a table
-- that already exists from an earlier run of this migration.
alter table global_task_sources drop constraint if exists global_task_sources_cadence_floor;
alter table global_task_sources add constraint global_task_sources_cadence_floor
  check (cadence is null or cadence >= interval '1 second');

-- fire_per_interval needs a cadence to count intervals against; a one-shot
-- (cadence IS NULL) with fire_per_interval is an incoherent state. Forbid it.
do $$
begin
  alter table global_task_sources add constraint cadence_lapse_coherence
    check (not (cadence is null and on_lapse = 'fire_per_interval'));
exception when duplicate_object then null;
end $$;

-- Indexes for efficient due-row queries and enabled filtering.
create index if not exists idx_global_task_sources_enabled_next_run
  on global_task_sources(enabled, next_run)
  where enabled = true;

create index if not exists idx_global_task_sources_dispatcher
  on global_task_sources(dispatcher_skill);

-- RLS: writes are service-role only; anon gets read-only visibility.
-- service_role BYPASSES RLS, so the advancer (service DSN) needs no write
-- policy. No policy grants INSERT/UPDATE/DELETE, so RLS denies writes to anon
-- and authenticated alike. The previous "Allow all for authenticated" policy
-- used `for all using (true)` with no TO clause — which defaults to PUBLIC and
-- silently granted write to every authenticated JWT client. Dropped.
-- DROP ... IF EXISTS + CREATE keeps policy setup idempotent (Postgres has no
-- CREATE POLICY IF NOT EXISTS).
alter table global_task_sources enable row level security;

drop policy if exists "Allow all for authenticated" on global_task_sources;
drop policy if exists "Anon select only" on global_task_sources;
create policy "Anon select only" on global_task_sources
  for select to anon using (true);
