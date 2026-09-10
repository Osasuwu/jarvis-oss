-- Minimal Postgres bootstrap for the DB-gated task_queue.enqueue() issue_number
-- CAS tests (tests/reactive_core/test_task_queue_issue_number_db.py), run by
-- the `pytest-db-task-queue` job in .github/workflows/pytest.yml. Issue #1085
-- S1-3.
--
-- Scope rationale: mirrors tests/ci/global_task_schema_bootstrap.sql's
-- approach — supabase/schema.sql (declarative, Supabase-specific) does not
-- apply cleanly to a stock postgres:16 image. This file bootstraps the
-- `task_queue` table shape as it exists BEFORE #1085 S1-1 (no issue_number
-- column, no partial unique index) so the CI job can apply the REAL migration
-- (supabase/migrations/20260811163000_add_task_queue_issue_number.sql) on top
-- and exercise production DDL, not a re-implementation. Keep this table shape
-- in sync with supabase/schema.sql's task_queue block if it changes.

-- ---------------------------------------------------------------------------
-- Supabase predefined roles. CI connects as the postgres superuser via
-- DATABASE_URL; these roles only need to EXIST so the `TO anon` RLS policy
-- clause resolves. NOLOGIN -- never authenticated against in CI.
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
-- `task_queue` -- pre-#1085 shape (supabase/schema.sql, minus issue_number
-- and its partial unique index, which the real migration adds on top).
-- ---------------------------------------------------------------------------
create table if not exists task_queue (
  id uuid primary key default gen_random_uuid(),

  goal text not null,
  scope_files text[] not null default '{}',

  priority int not null default 0,
  assignee text,

  status text not null default 'pending'
    check (status in ('pending', 'claimed', 'running', 'done', 'failed', 'parked', 'skipped_duplicate')),
  claimed_at timestamptz,
  completed_at timestamptz,
  escalated_reason text,

  idempotency_key text not null unique,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table task_queue enable row level security;

drop policy if exists "Allow all for authenticated" on task_queue;
create policy "Allow all for authenticated" on task_queue
  for all using (true) with check (true);

drop policy if exists "Allow all for anon" on task_queue;
create policy "Allow all for anon" on task_queue
  for all to anon using (true) with check (true);
