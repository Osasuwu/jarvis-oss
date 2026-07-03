-- Minimal Postgres bootstrap for the DB-gated global-task-advancer tests
-- (tests/test_global_task_advancer.py), run by the `pytest-db` job in
-- .github/workflows/pytest.yml. Issue #975.
--
-- Scope rationale: the advancer (scripts/advance-global-tasks.py) INSERTs
-- `global_task_due` rows into the LEGACY `events` table, which no Supabase
-- migration creates — it lives only in mcp-memory/schema.sql, a 133KB
-- Supabase-specific file (pgvector / pg_cron / auth schema / predefined roles)
-- that does not apply cleanly to a stock postgres:16 image. So this file
-- bootstraps ONLY what the advancer path touches:
--   1. the Supabase predefined roles the RLS policies reference, and
--   2. the legacy `events` table (faithful copy of mcp-memory/schema.sql).
--
-- The `global_task_sources` table itself is NOT created here — the CI job
-- applies its REAL migration
-- (supabase/migrations/20260615120000_create_global_task_sources.sql) on top
-- of this bootstrap, so the tests exercise production DDL (constraints, RLS,
-- indexes) rather than a re-implementation. Keep the `events` block below in
-- sync with mcp-memory/schema.sql if that table's shape changes.

-- ---------------------------------------------------------------------------
-- Supabase predefined roles. CI connects as the postgres superuser via
-- DATABASE_URL; these roles only need to EXIST so the `TO anon` / RLS policy
-- clauses in the events + global_task_sources DDL resolve. NOLOGIN — they are
-- never authenticated against in CI.
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Legacy `events` table — faithful copy of mcp-memory/schema.sql (the events
-- substrate the advancer writes to). The advancer relies on: event_type,
-- severity (low), repo, source, title, payload, and the partial UNIQUE index
-- on dedup_key that backs its `ON CONFLICT (dedup_key) DO NOTHING` dedup.
-- ---------------------------------------------------------------------------
create table if not exists events (
  id uuid primary key default gen_random_uuid(),

  event_type text not null,
  severity text not null default 'info'
    check (severity in ('critical', 'high', 'medium', 'low', 'info')),

  repo text not null,
  source text not null default 'github_action',

  title text not null,
  payload jsonb default '{}',

  processed boolean not null default false,
  processed_at timestamptz,
  processed_by text,
  action_taken text,

  state text not null default 'pending'
    check (state in ('pending', 'claimed', 'processed', 'parked')),
  dedup_key text,
  claimed_at timestamptz,
  claimed_by text,

  created_at timestamptz default now(),
  event_at timestamptz default now()
);

create unique index if not exists idx_events_dedup_key
  on events(dedup_key) where dedup_key is not null;

alter table events enable row level security;

drop policy if exists "Allow all for authenticated" on events;
create policy "Allow all for authenticated" on events
  for all using (true) with check (true);

drop policy if exists "Allow all for anon" on events;
create policy "Allow all for anon" on events
  for all to anon using (true) with check (true);
