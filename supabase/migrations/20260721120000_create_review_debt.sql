-- ===========================================================================
-- review_debt: sub-MAJOR code-review findings, collected + clustered (#1211).
--
-- The code-review plugin emits MEDIUM/INFO findings that never block a merge,
-- so they evaporate today. The review-debt collector (scripts/review_debt_
-- collector.py, run by .github/workflows/review-debt-collector.yml on merged
-- PRs) persists them here with weighted dedup, clusters by parent-directory
-- module_area, and auto-files one `review-debt-cluster` issue at threshold.
--
-- Additive migration — no change to existing tables. SHARED DB (co-tenant
-- redrobot); nothing here touches memory/events/task tables.
--
-- Dedup key EXCLUDES description text and line number (module_area + rule +
-- file) so the same defect recurring across PRs collapses to one row with an
-- incremented seen_count rather than duplicate rows.
-- ===========================================================================
create table if not exists review_debt (
  id            uuid primary key default gen_random_uuid(),
  dedup_key     text not null unique,
  module_area   text not null,
  severity      text not null,
  weight        numeric not null default 0.5,
  rule          text not null default '',
  file          text not null default '',
  seen_count    integer not null default 1,
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  -- 'open_debt'  = counting toward a cluster
  -- 'clustered'  = already folded into an open review-debt-cluster issue
  issued_state  text not null default 'open_debt',
  cluster_issue integer,        -- issue number once clustered (null until then)
  source_pr     text            -- ref of the most recent PR that surfaced it
);

create index if not exists idx_review_debt_module_area on review_debt (module_area);
create index if not exists idx_review_debt_issued_state on review_debt (issued_state);
create index if not exists idx_review_debt_last_seen on review_debt (last_seen_at desc);

-- Upsert RPC: increments seen_count on a repeat finding instead of duplicating.
-- PostgREST's merge-duplicates cannot express `seen_count = seen_count + 1`, so
-- the increment lives in SQL and CI calls this via POST /rest/v1/rpc/.
create or replace function review_debt_upsert(
  p_dedup_key   text,
  p_module_area text,
  p_severity    text,
  p_weight      numeric,
  p_rule        text,
  p_file        text,
  p_source_pr   text,
  p_seen_at     timestamptz default now()
) returns review_debt
language plpgsql
security invoker
set search_path = public
as $$
declare
  result review_debt;
begin
  insert into review_debt as rd (
    dedup_key, module_area, severity, weight, rule, file,
    source_pr, first_seen_at, last_seen_at
  )
  values (
    p_dedup_key, p_module_area, p_severity, p_weight, p_rule, p_file,
    p_source_pr, p_seen_at, p_seen_at
  )
  on conflict (dedup_key) do update
    set seen_count   = rd.seen_count + 1,
        last_seen_at = excluded.last_seen_at,
        source_pr    = excluded.source_pr,
        -- a re-surfaced finding re-opens the debt so it re-counts (AC5)
        issued_state = case
                         when rd.issued_state = 'clustered' then 'clustered'
                         else 'open_debt'
                       end
  returning rd.* into result;
  return result;
end;
$$;

-- RLS: allow-all convention (service_role bypasses; CI uses the anon key). The
-- collector needs INSERT/UPDATE via the RPC and SELECT for clustering. Unlike
-- events_canonical, no sandcastle actor gate — this is a CI-owned surface.
alter table review_debt enable row level security;
drop policy if exists "Allow all for authenticated" on review_debt;
drop policy if exists "Allow all for anon" on review_debt;
create policy "Allow all for authenticated" on review_debt
  for all using (true) with check (true);
create policy "Allow all for anon" on review_debt
  for all to anon using (true) with check (true);
