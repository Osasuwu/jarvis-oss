-- Minimal Postgres+pgvector bootstrap for the DB-gated find_consolidation_clusters
-- tests, run by the `pytest-db-pgvector` job in .github/workflows/pytest.yml.
-- Issue #1187.
--
-- Scope rationale: mcp-memory/schema.sql (133KB, Supabase-specific) does not
-- apply cleanly to a stock pgvector/pgvector image — its lone `CREATE EXTENSION`
-- (pg_cron, schema.sql:2825) needs `shared_preload_libraries` server config this
-- image doesn't set, and the file assumes Supabase's auth schema + predefined
-- roles already exist. Mirrors tests/ci/global_task_schema_bootstrap.sql's
-- approach: bootstrap only what the target function touches, then apply the
-- REAL migration on top so the tests exercise production DDL.
--
-- find_consolidation_clusters (supabase/migrations/20260715130000_rewrite_find_
-- consolidation_clusters.sql) touches: id, name, type, project_key (generated
-- from project), content, updated_at, embedding, expired_at, superseded_by,
-- deleted_at, valid_to, and idx_memories_embedding_hnsw for its LATERAL probe.
-- Keep this table shape in sync with mcp-memory/schema.sql if it changes.

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- Supabase predefined roles. CI connects as the postgres superuser via
-- DATABASE_URL; these roles only need to EXIST so any `TO anon`-style clause
-- in future DDL resolves. NOLOGIN — never authenticated against in CI.
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
-- Minimal `memories` table — faithful subset of mcp-memory/schema.sql's
-- columns for the fields find_consolidation_clusters reads.
-- ---------------------------------------------------------------------------
create table if not exists memories (
  id uuid default gen_random_uuid() primary key,

  type text not null check (type in ('user', 'project', 'decision', 'feedback', 'reference')),
  project text,

  name text not null,
  description text,
  content text not null,

  tags text[] default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique(project, name)
);

alter table memories add column if not exists embedding vector(512);

alter table memories add column if not exists project_key text
  generated always as (coalesce(project, '')) stored;

alter table memories add column if not exists content_updated_at timestamptz;
alter table memories add column if not exists valid_from timestamptz;
alter table memories add column if not exists valid_to timestamptz;
alter table memories add column if not exists expired_at timestamptz;

alter table memories add column if not exists superseded_by uuid
    references memories(id) on delete set null;

alter table memories add column if not exists deleted_at timestamptz;

create index if not exists idx_memories_embedding_hnsw
  on memories using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
