-- Jarvis Memory Schema for Supabase
-- Run this in the Supabase SQL Editor to create the table.

create table if not exists memories (
  id uuid default gen_random_uuid() primary key,

  -- Memory classification
  type text not null check (type in ('user', 'project', 'decision', 'feedback', 'reference')),
  project text,           -- null = global/cross-project, 'jarvis' = this project, etc.

  -- Content
  name text not null,     -- unique identifier within project scope
  description text,       -- one-line summary (used for relevance matching)
  content text not null,  -- full memory content

  -- Metadata
  tags text[] default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  -- One memory per name per project scope
  unique(project, name)
);

-- Indexes for common query patterns
create index if not exists idx_memories_project on memories(project);
create index if not exists idx_memories_type on memories(type);
create index if not exists idx_memories_tags on memories using gin(tags);

-- Voyage AI embedding storage (512-dim vectors via pgvector extension)
alter table memories add column if not exists embedding vector(512);

-- Read tracking for temporal scoring (Memory 2.0)
alter table memories add column if not exists last_accessed_at timestamptz;

-- Partial index to efficiently find rows missing embeddings (for backfill queries)
create index if not exists idx_memories_no_embedding on memories(id) where embedding is null;

-- Full-text search index for keyword recall
alter table memories add column if not exists fts tsvector
  generated always as (
    to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, '') || ' ' || coalesce(content, ''))
  ) stored;

create index if not exists idx_memories_fts on memories using gin(fts);

-- Generated project scope key for tiebreakers / grouping on a non-null text.
-- Applied in production via an out-of-band migration; re-declared here so
-- schema.sql matches the live DB. Must stay in update_updated_at's strip
-- list (GENERATED ALWAYS STORED cols appear NULL in BEFORE UPDATE NEW).
alter table memories add column if not exists project_key text
  generated always as (coalesce(project, '')) stored;

-- Auto-update updated_at on changes
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists memories_updated_at on memories;
create trigger memories_updated_at
  before update on memories
  for each row execute function update_updated_at();

-- Row Level Security (optional but recommended)
alter table memories enable row level security;

-- Allow all operations for authenticated users (single-user system)
create policy "Allow all for authenticated" on memories
  for all using (true) with check (true);

-- Anon access (sandcastle agent path) — INSERT/UPDATE/DELETE all gated by
-- source_provenance prefix. Slice 3 (#542) gated INSERT; slice 3.5 (#565)
-- extended the gate to UPDATE+DELETE so anon cannot wipe rows or forge the
-- provenance column. Decisions 228a2d9b (slice 3), f3b85eeb (slice 3.5).
-- Service-role bypasses RLS automatically; host orchestrator unaffected.
create policy "Anon select" on memories
  for select to anon using (true);
create policy "Anon sandcastle update" on memories
  for update to anon
  using (source_provenance like 'sandcastle:%')
  with check (source_provenance like 'sandcastle:%');
create policy "Anon sandcastle delete" on memories
  for delete to anon
  using (source_provenance like 'sandcastle:%');
create policy "Anon sandcastle insert" on memories
  for insert to anon
  with check (source_provenance like 'sandcastle:%');


-- =========================================================================
-- Goals table — Jarvis 2.0 Pillar 1: Goals & Strategic Context
-- =========================================================================

create table if not exists goals (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  project text,
  direction text,
  priority text not null default 'P1'
    check (priority in ('P0', 'P1', 'P2')),
  status text not null default 'active'
    check (status in ('active', 'achieved', 'paused', 'abandoned')),

  why text,
  success_criteria jsonb default '[]',

  deadline date,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  closed_at timestamptz,

  progress jsonb default '[]',
  progress_pct integer default 0
    check (progress_pct >= 0 and progress_pct <= 100),

  risks jsonb default '[]',
  owner_focus text,
  jarvis_focus text,

  parent_id uuid references goals(id) on delete set null,

  outcome text,
  lessons text
);

create index if not exists idx_goals_status on goals(status);
create index if not exists idx_goals_project on goals(project);
create index if not exists idx_goals_priority on goals(priority);
create index if not exists idx_goals_parent on goals(parent_id);

-- Auto-update updated_at
create or replace function update_goals_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists goals_updated_at on goals;
create trigger goals_updated_at
  before update on goals
  for each row execute function update_goals_updated_at();

-- RLS
alter table goals enable row level security;

create policy "Allow all for authenticated" on goals
  for all using (true) with check (true);

create policy "Allow all for anon" on goals
  for all to anon using (true) with check (true);


-- =========================================================================
-- Events table — Jarvis Pillar 2: Event-Driven Perception
-- GitHub Actions write events here, orchestrator reads them.
-- =========================================================================

create table if not exists events (
  id uuid primary key default gen_random_uuid(),

  -- Event classification
  event_type text not null,          -- 'ci_failure', 'security_alert', 'pr_approved', 'deployment', etc.
  severity text not null default 'info'
    check (severity in ('critical', 'high', 'medium', 'low', 'info')),

  -- Source
  repo text not null,                -- 'your-username/your-repo', 'your-username/your-second-repo', etc.
  source text not null default 'github_action',  -- 'github_action', 'webhook', 'manual'

  -- Content
  title text not null,               -- one-line summary
  payload jsonb default '{}',        -- structured event data (PR number, workflow name, alert details)

  -- Processing
  processed_at timestamptz,
  processed_by text,                 -- 'autonomous-loop', 'risk-radar', 'manual'
  action_taken text,                 -- what was done in response

  -- Event queue FSM (#739)
  state text not null default 'pending'
    check (state in ('pending', 'claimed', 'processed', 'parked')),
  dedup_key text,                    -- sha256 of identifying fields; unique when set
  claimed_at timestamptz,
  claimed_by text,                   -- who claimed it (e.g. 'orchestrator', 'wake_driver')

  -- Timestamps
  created_at timestamptz default now(),
  event_at timestamptz default now() -- when the event actually occurred (may differ from insert time)
);

-- Indexes
create index if not exists idx_events_repo on events(repo);
create index if not exists idx_events_type on events(event_type);
create index if not exists idx_events_created on events(created_at desc);
create unique index if not exists idx_events_dedup_key on events(dedup_key) where dedup_key is not null;
create index if not exists idx_events_pending on events(state, severity, created_at)
  where state = 'pending';

-- RLS
alter table events enable row level security;

create policy "Allow all for authenticated" on events
  for all using (true) with check (true);

create policy "Allow all for anon" on events
  for all to anon using (true) with check (true);


-- =========================================================================
-- Event queue FSM: NOTIFY trigger + RPCs (#739)
-- =========================================================================

create or replace function notify_events_insert()
returns trigger as $$
begin
  perform pg_notify(
    'events',
    json_build_object(
      'id',         new.id,
      'event_type', new.event_type,
      'severity',   new.severity,
      'title',      new.title,
      'repo',       new.repo
    )::text
  );
  return new;
end;
$$ language plpgsql;

drop trigger if exists events_notify on events;
create trigger events_notify
  after insert on events
  for each row execute function notify_events_insert();

-- claim_next: atomically claim the highest-priority pending event
create or replace function claim_next(claimer text)
returns setof events
language plpgsql
as $$
declare
  event_row events%ROWTYPE;
begin
  select * into event_row
  from events
  where state = 'pending'
  order by
    case severity
      when 'critical' then 0
      when 'high'     then 1
      when 'medium'   then 2
      when 'low'      then 3
      when 'info'     then 4
    end asc,
    created_at asc
  limit 1
  for update skip locked;

  if found then
    update events
    set state = 'claimed',
        claimed_at = now(),
        claimed_by = claimer
    where id = event_row.id
    returning * into event_row;
    return next event_row;
  end if;
  return;
end;
$$;

-- mark_processed: transition claimed → processed
create or replace function mark_processed(
  event_id uuid,
  processor text,
  action_taken text default ''
)
returns boolean
language plpgsql
as $$
begin
  update events
  set state = 'processed',
      processed_at = now(),
      processed_by = processor,
      action_taken = mark_processed.action_taken
  where id = event_id and state = 'claimed';
  return found;
end;
$$;

-- park_event: transition claimed → parked (blocked on dependency)
create or replace function park_event(
  event_id uuid,
  reason text default ''
)
returns boolean
language plpgsql
as $$
begin
  update events
  set state = 'parked',
      action_taken = park_event.reason
  where id = event_id and state = 'claimed';
  return found;
end;
$$;

-- requeue_event: transition claimed/parked → pending (retry)
create or replace function requeue_event(
  event_id uuid,
  reason text default ''
)
returns boolean
language plpgsql
as $$
begin
  update events
  set state = 'pending',
      claimed_at = null,
      claimed_by = null,
      action_taken = requeue_event.reason
  where id = event_id and (state = 'claimed' or state = 'parked');
  return found;
end;
$$;


-- =========================================================================
-- Memory Links — Memory 2.0: Graph Relationships
-- Auto-generated on memory_store, tracks related/superseded/consolidated
-- =========================================================================

create table if not exists memory_links (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references memories(id) on delete cascade,
  target_id uuid not null references memories(id) on delete cascade,
  link_type text not null default 'related'
    check (link_type in ('related', 'supersedes', 'consolidates')),
  strength float not null default 1.0
    check (strength >= 0 and strength <= 1),
  created_at timestamptz default now(),
  unique(source_id, target_id, link_type),
  check(source_id != target_id)
);

create index if not exists idx_memory_links_source on memory_links(source_id);
create index if not exists idx_memory_links_target on memory_links(target_id);
create index if not exists idx_memory_links_type on memory_links(link_type);

alter table memory_links enable row level security;

create policy "Allow all for authenticated" on memory_links
  for all using (true) with check (true);

create policy "Allow all for anon" on memory_links
  for all to anon using (true) with check (true);


-- =========================================================================
-- Memory Consolidation — Memory 2.0: Merge similar memories
-- Finds clusters of 3+ memories with cosine similarity >= 0.80
-- Returns clusters for LLM-driven merge (content merging is not pure SQL)
-- =========================================================================

-- Find groups of memories that should be consolidated.
-- Phase 5.1c: the `live` CTE applies Phase 1 default-recall semantics at the
-- source so consumers never see stale memories in a cluster. Legacy
-- `%_archived` types are also excluded defensively (0 rows as of 2026-04-19,
-- left in place so a stray row from old logic cannot poison consolidation).
--
-- Rewritten #1187: the prior O(N^2) self-join (`live a join live b on
-- a.id < b.id and a.type = b.type`) timed out (57014) once live-memory
-- volume grew (2054 rows -> 2M+ pair comparisons). Replaced with:
--   1. Strict (type, project_key) partitioning (was type-only — cross-project
--      memories of the same type could cluster together).
--   2. A bare HNSW LATERAL probe (limit 40, zero predicates inside the
--      LATERAL — all filters applied outside) so the planner reliably uses
--      idx_memories_embedding_hnsw instead of falling back to a seq scan.
--   3. Disjoint connected components via label propagation / union-find
--      over a temp edge table, replacing the old overlapping anchor-star
--      clustering (a memory could appear in multiple clusters if it was
--      similar to two different anchors).
-- `analyze live_tmp` after populating it is required — without it the
-- planner uses default cardinality stats for the fresh temp table and
-- picks a catastrophic join plan for the edge-generation insert (verified
-- empirically: identical query times out at 2min without ANALYZE, ~2.9s
-- with it, against 2054 live rows / ~51k qualifying edges).
create or replace function find_consolidation_clusters(
  min_cluster_size int default 3,
  sim_threshold float default 0.80
)
returns table (
  cluster_id int,
  memory_id uuid,
  memory_name text,
  memory_type text,
  content text,
  similarity float,
  updated_at timestamptz
)
language plpgsql
as $$
declare
  i int := 0;
begin
  set local hnsw.ef_search = 80;

  create temp table if not exists live_tmp (
    id uuid primary key,
    name text,
    type text,
    project_key text,
    content text,
    updated_at timestamptz,
    embedding vector,
    label uuid
  ) on commit drop;
  truncate live_tmp;

  create temp table if not exists cc_edges (
    id_a uuid,
    id_b uuid,
    sim float
  ) on commit drop;
  truncate cc_edges;

  insert into live_tmp (id, name, type, project_key, content, updated_at, embedding, label)
  select m.id, m.name, m.type, m.project_key, m.content, m.updated_at, m.embedding, m.id
  from memories m
  where m.embedding is not null
    and m.expired_at is null
    and m.superseded_by is null
    and m.deleted_at is null
    and (m.valid_to is null or m.valid_to > now())
    and m.type not like '%\_archived' escape '\';

  analyze live_tmp;

  -- Bare HNSW probe: no predicates inside the LATERAL so the planner uses
  -- idx_memories_embedding_hnsw; type/project_key/similarity filters applied
  -- outside against the pre-filtered live_tmp set.
  insert into cc_edges (id_a, id_b, sim)
  select a.id, nb.neighbor_id, nb.sim
  from live_tmp a
  cross join lateral (
    select b.id as neighbor_id, 1 - (b.embedding <=> a.embedding) as sim
    from memories b
    order by b.embedding <=> a.embedding
    limit 40
  ) nb
  join live_tmp b2 on b2.id = nb.neighbor_id
  where nb.neighbor_id <> a.id
    and b2.type = a.type
    and b2.project_key = a.project_key
    and nb.sim >= sim_threshold;

  analyze cc_edges;

  -- Label propagation to disjoint connected components. uuid has no min()
  -- aggregate, so the running-minimum label is resolved via a text cast.
  loop
    i := i + 1;
    with prop as (
      select e.id_a as id, least(la.label, lb.label) as new_label
      from cc_edges e
      join live_tmp la on la.id = e.id_a
      join live_tmp lb on lb.id = e.id_b
      where la.label <> lb.label
      union all
      select e.id_b as id, least(la.label, lb.label) as new_label
      from cc_edges e
      join live_tmp la on la.id = e.id_a
      join live_tmp lb on lb.id = e.id_b
      where la.label <> lb.label
    ),
    agg as (
      select id, min(new_label::text)::uuid as new_label from prop group by id
    )
    update live_tmp lt
    set label = agg.new_label
    from agg
    where agg.id = lt.id and agg.new_label < lt.label;

    exit when not found;
    if i > 1000 then
      raise exception 'find_consolidation_clusters: label propagation did not converge after % iterations', i;
    end if;
  end loop;

  return query
  with sims as (
    select id_a as id, max(sim) as best_sim from cc_edges group by id_a
    union all
    select id_b as id, max(sim) as best_sim from cc_edges group by id_b
  ),
  best as (
    select id, max(best_sim) as similarity from sims group by id
  ),
  comp_sizes as (
    select label, count(*) as comp_size from live_tmp group by label
  ),
  qualifying as (
    select lt.id, lt.name, lt.type, lt.content, lt.updated_at, lt.label,
           coalesce(b.similarity, 0) as similarity,
           row_number() over (partition by lt.label order by lt.updated_at desc, lt.id) as rn
    from live_tmp lt
    join comp_sizes cs on cs.label = lt.label
    left join best b on b.id = lt.id
    where cs.comp_size >= min_cluster_size
  ),
  -- Cap components >10 members to the 10 most recently updated (#1187 AC).
  capped as (
    select * from qualifying where rn <= 10
  )
  select
    dense_rank() over (order by capped.label)::int as cluster_id,
    capped.id as memory_id,
    capped.name as memory_name,
    capped.type as memory_type,
    capped.content as content,
    capped.similarity as similarity,
    capped.updated_at as updated_at
  from capped
  order by 1, capped.updated_at desc;
end;
$$;

-- Archive superseded memories (Phase 5.1c): set `expired_at` instead of
-- renaming type. Leaves type intact so type-filtered recall isn't poisoned
-- (audit gap #6 in docs/design/memory-overhaul.md). Idempotent — skips rows
-- already expired and doesn't double-prefix the description.
create or replace function archive_memories(memory_ids uuid[])
returns int
language plpgsql volatile
as $$
declare
  archived_count int;
begin
  update memories
  set expired_at = coalesce(expired_at, now()),
      description = case
        when description like '[ARCHIVED%' then description
        else '[ARCHIVED — consolidated] ' || coalesce(description, '')
      end
  where id = any(memory_ids)
    and expired_at is null;
  get diagnostics archived_count = row_count;
  return archived_count;
end;
$$;


-- =========================================================================
-- Task Outcomes — Pillar 3: Outcome Tracking & Learning
-- Records results of delegations, research, fixes, autonomous actions.
-- =========================================================================

create table if not exists task_outcomes (
  id uuid primary key default gen_random_uuid(),

  -- What was done
  task_type text not null
    check (task_type in ('delegation', 'research', 'fix', 'review', 'autonomous')),
  task_description text not null,
  outcome_status text not null default 'pending'
    check (outcome_status in ('pending', 'success', 'partial', 'failure', 'unknown')),
  outcome_summary text,

  -- Links
  goal_slug text,           -- related goal
  project text,             -- project scope
  issue_url text,           -- GitHub issue URL
  pr_url text,              -- GitHub PR URL

  -- Quality signals
  tests_passed boolean,
  pr_merged boolean,
  quality_score integer check (quality_score is null or (quality_score >= 0 and quality_score <= 100)),

  -- Learning
  lessons text,
  pattern_tags text[] default '{}',

  -- Which memory most informed this outcome's decision (Phase 5
  -- Metacognition / Confidence Calibration, #251). FK to memories(id) —
  -- NOT record_decision's returned episode UUID (episodes.id); see #660.
  -- Nullable — outcomes recorded before calibration work won't have it,
  -- and some outcomes (e.g. pure research tasks) don't trace to a single
  -- memory.
  memory_id uuid references memories(id) on delete set null,

  -- Verification
  verified_at timestamptz,  -- when outcome was verified (e.g. PR merged check)

  -- Timestamps
  created_at timestamptz default now(),

  -- Provenance prefix used by RLS to gate anon INSERT (slice 3, #542,
  -- decision 228a2d9b). Nullable for legacy rows; new sandcastle-agent
  -- writes must set 'sandcastle:<...>' to be accepted via anon key.
  source_provenance text
);

create index if not exists idx_task_outcomes_project on task_outcomes(project);
create index if not exists idx_task_outcomes_goal on task_outcomes(goal_slug);
create index if not exists idx_task_outcomes_status on task_outcomes(outcome_status);
create index if not exists idx_task_outcomes_created on task_outcomes(created_at desc);
create index if not exists idx_task_outcomes_tags on task_outcomes using gin(pattern_tags);

-- RLS
alter table task_outcomes enable row level security;

create policy "Allow all for authenticated" on task_outcomes
  for all using (true) with check (true);

-- Anon access — INSERT/UPDATE/DELETE gated by source_provenance prefix
-- (slices 3 + 3.5, #542 + #565).
create policy "Anon select" on task_outcomes
  for select to anon using (true);
create policy "Anon sandcastle update" on task_outcomes
  for update to anon
  using (source_provenance like 'sandcastle:%')
  with check (source_provenance like 'sandcastle:%');
create policy "Anon sandcastle delete" on task_outcomes
  for delete to anon
  using (source_provenance like 'sandcastle:%');
create policy "Anon sandcastle insert" on task_outcomes
  for insert to anon
  with check (source_provenance like 'sandcastle:%');


-- =========================================================================
-- RPC Functions — Memory 2.0
-- =========================================================================

-- HNSW index for fast vector similarity search (pgvector)
-- Without this, find_similar_memories() does slow sequential scan.
create index if not exists idx_memories_embedding_hnsw
  on memories using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- Semantic search for hybrid recall (used by memory_recall's RRF pipeline)
-- Returns memories ranked by cosine similarity via HNSW index.
create or replace function match_memories(
    query_embedding vector,
    match_limit int default 10,
    similarity_threshold float default 0.3,
    filter_project text default null,
    filter_type text default null
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, last_accessed_at timestamptz,
    similarity float
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.last_accessed_at,
           1 - (m.embedding <=> query_embedding) as similarity
    from memories m
    where m.embedding is not null
      and 1 - (m.embedding <=> query_embedding) >= similarity_threshold
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
    order by m.embedding <=> query_embedding
    limit match_limit;
$$;


-- Keyword search for hybrid recall (used by memory_recall's RRF pipeline)
-- Uses full-text search (tsvector) with ranking.
create or replace function keyword_search_memories(
    search_query text,
    match_limit int default 10,
    filter_project text default null,
    filter_type text default null
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, last_accessed_at timestamptz,
    rank real
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.last_accessed_at,
           ts_rank(m.fts, websearch_to_tsquery('english', search_query)) as rank
    from memories m
    where m.fts @@ websearch_to_tsquery('english', search_query)
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
    order by rank desc
    limit match_limit;
$$;



-- Find memories semantically similar to a given embedding (for auto-linking on store)
-- Uses HNSW index on memories.embedding
create or replace function find_similar_memories(
    query_embedding vector,
    exclude_id uuid,
    match_limit int default 5,
    similarity_threshold float default 0.6,
    filter_type text default null
)
returns table(id uuid, name text, type text, project text, similarity float)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           1 - (m.embedding <=> query_embedding) as similarity
    from memories m
    where m.embedding is not null
      and m.id != exclude_id
      and 1 - (m.embedding <=> query_embedding) >= similarity_threshold
      and (filter_type is null or m.type = filter_type)
    order by m.embedding <=> query_embedding
    limit match_limit;
$$;

-- Get 1-hop linked memories for graph-aware recall
create or replace function get_linked_memories(
    memory_ids uuid[],
    link_types text[] default null
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz,
    link_type text, link_strength float, linked_from uuid
)
language sql stable as $$
    -- Outer query re-orders deduped results by strength
    select * from (
        -- DISTINCT ON keeps only the strongest link per target memory
        select distinct on (sub.id)
            sub.id, sub.name, sub.type, sub.project, sub.description,
            sub.content, sub.tags, sub.updated_at,
            sub.link_type, sub.link_strength, sub.linked_from
        from (
            select m.id, m.name, m.type, m.project, m.description, m.content, m.tags, m.updated_at,
                   l.link_type, l.strength as link_strength, l.source_id as linked_from
            from memory_links l
            join memories m on m.id = l.target_id
            where l.source_id = any(memory_ids)
              and not (l.target_id = any(memory_ids))
              and (link_types is null or l.link_type = any(link_types))
            union all
            select m.id, m.name, m.type, m.project, m.description, m.content, m.tags, m.updated_at,
                   l.link_type, l.strength as link_strength, l.target_id as linked_from
            from memory_links l
            join memories m on m.id = l.source_id
            where l.target_id = any(memory_ids)
              and not (l.source_id = any(memory_ids))
              and (link_types is null or l.link_type = any(link_types))
        ) sub
        order by sub.id, sub.link_strength desc, sub.link_type, sub.linked_from
    ) deduped
    order by deduped.link_strength desc
    limit 10;
$$;


-- Update last_accessed_at for temporal scoring (called fire-and-forget on recall)
create or replace function touch_memories(memory_ids uuid[])
returns void
language sql volatile as $$
    update memories
    set last_accessed_at = now()
    where id = any(memory_ids);
$$;


-- =========================================================================
-- Phase 0 (memory overhaul, Osasuwu/jarvis#185) — lifecycle + provenance
-- fields. Non-breaking: all columns have defaults; server.py still reads
-- the old columns until Phase 1.
--
-- Rationale: every column below is driven by either (a) a convergent signal
-- from the research synthesis — production systems (Zep/Graphiti, Mem0,
-- A-MEM, LangMem) agreeing with theory (AGM, JTMS, ACT-R, CLS), or (b) a
-- production risk the research flagged that we hadn't noticed (embedding
-- migration). See docs/design/memory-overhaul.md §2.
-- =========================================================================

-- Bi-temporal: valid time ≠ transaction time (Snodgrass; Zep/Graphiti)
-- - content_updated_at: set only when the memory's content/description/tags
--   actually change. Phase 1 will drive decay off this instead of updated_at
--   (session-start reads currently bump updated_at and defeat decay).
-- - valid_from / valid_to: when the fact was true in the world.
-- - expired_at: when we stopped believing it (transaction time soft-delete).
alter table memories add column if not exists content_updated_at timestamptz;
alter table memories add column if not exists valid_from timestamptz;
alter table memories add column if not exists valid_to timestamptz;
alter table memories add column if not exists expired_at timestamptz;

-- Direct supersession pointer for chain walks in recall (Phase 1 filter).
-- memory_links.link_type='supersedes' still holds the graph; this is a
-- denormalized shortcut that lets recall filter in one join.
alter table memories add column if not exists superseded_by uuid
    references memories(id) on delete set null;

-- Entrenchment (Gärdenfors) / confidence. 0.0–1.0.
-- User-stated = 1.0, inferred from single session = 0.5, guessed from one
-- tool output = 0.2. Phase 2's classifier writes this; low-confidence memories
-- get aggressive decay and are hidden from default recall.
alter table memories add column if not exists confidence real default 0.5
    check (confidence is null or (confidence >= 0 and confidence <= 1));

-- JTMS justification — we cannot revise what we cannot attribute.
-- Free-form text so we can record arbitrary provenance (URL, tool name,
-- session id, actor namespace). Phase 2 classifier + Phase 4 episodic
-- layer will populate consistently.
alter table memories add column if not exists source_provenance text;

-- Profiles (overwrite-single-row) vs collections (append) distinction,
-- from LangMem. Eliminates supersession bugs by construction for rows where
-- the owner wants single-instance semantics (owner_preferences, device_config).
alter table memories add column if not exists single_instance boolean
    not null default false;

-- Embedding model migration safety. The day we upgrade from voyage-3-lite,
-- every cosine similarity in the DB becomes incomparable across models. We
-- need dual-column support during migration; these columns let us filter.
alter table memories add column if not exists embedding_model text
    default 'voyage-3-lite';
alter table memories add column if not exists embedding_version text
    default 'v1';

-- Indexes that Phase 1+ will rely on:
create index if not exists idx_memories_superseded_by
    on memories(superseded_by) where superseded_by is not null;
create index if not exists idx_memories_lifecycle_live
    on memories(type, project) where expired_at is null and superseded_by is null;
create index if not exists idx_memories_provenance
    on memories(source_provenance) where source_provenance is not null;

-- One-time backfill: copy updated_at -> content_updated_at for existing rows.
-- After this, Phase 1 will update the trigger to only bump content_updated_at
-- on actual content changes.
update memories
set content_updated_at = updated_at
where content_updated_at is null;

-- Denormalize existing memory_links.link_type='supersedes' edges into the
-- new superseded_by column. Convention: link.source supersedes link.target,
-- so the older (target) memory's superseded_by points to the newer (source).
update memories m
set superseded_by = l.source_id
from memory_links l
where l.link_type = 'supersedes'
  and l.target_id = m.id
  and m.superseded_by is null;


-- =========================================================================
-- Phase 1 (memory overhaul, Osasuwu/jarvis#185) — recall correctness
--
-- Goal: drive must_not violations to 0, stabilize MRR.
--
-- Changes:
--   1. Split timestamps properly:
--      - updated_at: bumped on any UPDATE (existing trigger, unchanged)
--      - content_updated_at: bumped only when name/description/content/tags
--        actually change (new trigger). This becomes the decay axis.
--      - last_accessed_at: set by touch_memories only (existing).
--      The old behavior bumped updated_at on every recall (via touch), which
--      meant temporal scoring reshuffled itself on every run → MRR noise.
--   2. Add default recall filter: exclude expired_at IS NOT NULL,
--      superseded_by IS NOT NULL, and (valid_to IS NOT NULL AND valid_to <= now()).
--   3. Add show_history flag to bypass the filter (for audit/debug queries).
--   4. Return content_updated_at from RPCs so server.py can use it for decay.
-- =========================================================================

-- Trigger: only bump content_updated_at when the memory's content-bearing
-- fields actually change. This is the field Phase 1 recall uses for decay,
-- so it must not be perturbed by touch_memories (which updates only
-- last_accessed_at but fires the generic update trigger).
create or replace function update_content_updated_at()
returns trigger as $$
begin
  if (new.name is distinct from old.name)
     or (new.description is distinct from old.description)
     or (new.content is distinct from old.content)
     or (new.tags is distinct from old.tags)
  then
    new.content_updated_at = now();
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists memories_content_updated_at on memories;
create trigger memories_content_updated_at
  before update on memories
  for each row execute function update_content_updated_at();

-- Replace match_memories: add lifecycle filter + show_history + return
-- content_updated_at. Signature adds two trailing optional params so
-- existing callers keep working.
drop function if exists match_memories(vector, int, float, text, text);
drop function if exists match_memories(vector, int, float, text, text, boolean);
create or replace function match_memories(
    query_embedding vector,
    match_limit int default 10,
    similarity_threshold float default 0.3,
    filter_project text default null,
    filter_type text default null,
    show_history boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    similarity float
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.content_updated_at, m.last_accessed_at,
           1 - (m.embedding <=> query_embedding) as similarity
    from memories m
    where m.embedding is not null
      and 1 - (m.embedding <=> query_embedding) >= similarity_threshold
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
      and (show_history
           or (m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
    order by m.embedding <=> query_embedding
    limit match_limit;
$$;

-- Replace keyword_search_memories: same lifecycle filter + content_updated_at.
drop function if exists keyword_search_memories(text, int, text, text);
drop function if exists keyword_search_memories(text, int, text, text, boolean);
create or replace function keyword_search_memories(
    search_query text,
    match_limit int default 10,
    filter_project text default null,
    filter_type text default null,
    show_history boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    rank real
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.content_updated_at, m.last_accessed_at,
           ts_rank(m.fts, websearch_to_tsquery('english', search_query)) as rank
    from memories m
    where m.fts @@ websearch_to_tsquery('english', search_query)
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
      and (show_history
           or (m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
    order by rank desc
    limit match_limit;
$$;


-- =========================================================================
-- Phase 2b (memory overhaul, Osasuwu/jarvis#185) — write-time classifier
--
-- Goal: replace the SUPERSEDE_SIM_THRESHOLD heuristic with an LLM decision
-- (Mem0-style ADD / UPDATE / DELETE / NOOP). Low-confidence decisions land
-- in this review queue instead of being silently applied — same idea as
-- LangMem's background mode, but synchronous on the write path because we
-- need the decision to know whether to mark a neighbor superseded.
--
-- Workflow:
--   1. memory_store computes embedding, upserts the row (stored_id).
--   2. Looks up top-K similar neighbors above CLASSIFIER_TRIGGER_SIM (~0.70).
--   3. If any → calls Haiku-4.5 with {candidate, neighbors} → JSON decision.
--   4. confidence >= APPLY_THRESHOLD (~0.7): apply the decision
--      (UPDATE/DELETE marks the target as superseded/expired).
--   5. confidence < APPLY_THRESHOLD: write to memory_review_queue
--      with status='pending' so the owner can audit before applying.
--
-- We always insert the candidate first (never lose data). DELETE/NOOP at
-- low confidence still become "ADD-and-queue-the-decision-for-review".
-- =========================================================================

create table if not exists memory_review_queue (
  id uuid primary key default gen_random_uuid(),

  -- The just-stored candidate that triggered classification.
  candidate_id uuid references memories(id) on delete cascade,

  -- Classifier output.
  decision text not null check (decision in ('ADD', 'UPDATE', 'DELETE', 'NOOP')),
  target_id uuid references memories(id) on delete set null,
  confidence real not null check (confidence >= 0 and confidence <= 1),
  reasoning text,

  -- Provenance for the decision itself (so we can audit which model said what).
  classifier_model text default 'claude-haiku-4-5',
  neighbors_seen jsonb,            -- snapshot of {id, name, similarity} fed to model

  -- Lifecycle of the queue entry.
  -- pending: awaiting owner review
  -- approved: owner approved, will be applied (or already applied)
  -- rejected: owner said no, decision discarded
  -- auto_applied: confidence was high enough to skip review (recorded for audit)
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected', 'auto_applied')),

  applied_at timestamptz,           -- when the decision actually mutated the row
  reviewed_at timestamptz,
  reviewed_by text,                 -- 'owner' / 'autonomous' / 'consolidation_job'

  created_at timestamptz default now()
);

create index if not exists idx_review_queue_pending
    on memory_review_queue(created_at desc) where status = 'pending';
create index if not exists idx_review_queue_candidate
    on memory_review_queue(candidate_id);

alter table memory_review_queue enable row level security;

create policy "Allow all for authenticated" on memory_review_queue
  for all using (true) with check (true);

create policy "Allow all for anon" on memory_review_queue
  for all to anon using (true) with check (true);


-- =========================================================================
-- Phase 2c (memory overhaul, Osasuwu/jarvis#185, #196) — provenance required
--
-- Goal: close the audit gap left by Phase 2. Every memory must carry
-- `source_provenance` so we can revise beliefs knowing who/what produced
-- them (JTMS principle — can't revise what you can't attribute).
--
-- Before 2c: `source_provenance` was a nullable column, populated when
-- convenient. In practice most rows were null, which defeated the purpose.
-- After 2c: NOT NULL enforced. MCP server rejects writes missing it.
-- Pre-policy rows get `legacy:pre-2c` so they're distinguishable from
-- policy-compliant data without blocking the constraint.
--
-- Coordinates with Phase 4 (#197): episode extractor will populate
-- `source_provenance = 'episode:<episode_id>'` — contract only, no overlap.
-- =========================================================================

-- Backfill pre-policy rows with a distinguishing marker. Literal string
-- (not '') so future queries can filter `where source_provenance =
-- 'legacy:pre-2c'` to find rows with no real provenance. Also catches
-- whitespace-only values — prior callers could persist '   ' which is
-- truthy in Python but carries no attribution.
update memories
set source_provenance = 'legacy:pre-2c'
where source_provenance is null
   or btrim(source_provenance) = '';

-- Enforce going forward. Any caller bypassing the MCP server with a raw
-- INSERT now errors out — which is the desired failure mode (those writes
-- also skip the embedding + classifier pipeline and shouldn't exist).
alter table memories
    alter column source_provenance set not null;

-- Soft-delete column. server.py has always written/filtered on this (store
-- clears it, recall/list/get/delete filter `is deleted_at null`), but the
-- column was never declared in schema.sql — meaning a fresh provision
-- would error at runtime. Add as part of the 2c hardening pass since the
-- provenance work is the first time the audit story is end-to-end.
alter table memories add column if not exists deleted_at timestamptz;

-- Partial index on tombstones. Mirrors the index already present in the
-- live DB — small set, fast to scan when purging or auditing deletes.
-- Live-row queries don't need a dedicated index: they combine deleted_at
-- with type/project/lifecycle filters already covered by existing indexes.
create index if not exists idx_memories_deleted_at
  on memories(deleted_at) where deleted_at is not null;


-- =========================================================================
-- Phase 4 (memory overhaul, Osasuwu/jarvis#197) — episodic layer
--
-- Raw "what happened" buffer that an async extractor distills into candidate
-- memories. Mirrors the episodic ↔ semantic separation from CLS theory and
-- production systems (Letta tiered memory, A-MEM, LangMem background mode):
-- non-lossy episodic buffer, consolidation happens offline.
--
-- Write path: hooks / skills / autonomous code insert rows here cheaply.
-- Read path: episode_extractor.py batches unprocessed rows, synthesizes
-- candidate memories via Haiku, and writes them through the existing
-- memory_store flow with source_provenance='episode:<id>'.
-- =========================================================================

create table if not exists episodes (
  id uuid primary key default gen_random_uuid(),

  -- Who/what produced this episode.
  -- Convention: 'session:<id>', 'scheduled:<skill>', 'hook:<name>',
  -- 'skill:<name>', 'autonomous:<skill>'.
  actor text not null,

  -- Shape of the payload. Extractor may prompt differently per kind.
  kind text not null
    check (kind in ('tool_call', 'decision', 'user_message', 'assistant_message', 'observation', 'decision_made')),

  -- Arbitrary structured content. Schema is intentionally loose — episodes
  -- are raw material, not normalized facts.
  payload jsonb not null default '{}',

  -- Transaction time: when the episode was recorded.
  created_at timestamptz not null default now(),

  -- Extractor marks this when it has consumed the episode (success or skip).
  -- NULL = still in the backlog.
  processed_at timestamptz
);

-- Backlog scan — partial index so the extractor's "fetch next batch" query
-- stays cheap regardless of processed-history size.
create index if not exists idx_episodes_unprocessed
  on episodes(created_at) where processed_at is null;

-- Per-actor filtering for debugging / per-source stats.
create index if not exists idx_episodes_actor on episodes(actor);

-- Chronological audit.
create index if not exists idx_episodes_created on episodes(created_at desc);

-- #1269: decision_list recovery query — partial expression index over the
-- session id stamped into decision_made payloads by the PreToolUse gate.
-- session_id lives in payload jsonb (not a column) so the C17 dual-write
-- to events_canonical carries it without schema changes there.
create index if not exists idx_episodes_payload_session_id
  on episodes ((payload->>'session_id'))
  where (payload->>'session_id') is not null;

-- #1423: session_id is demoted to forensic grouping metadata — the
-- decision_list recovery key is (project, cwd, since), because a
-- resume/compaction always mints a new session_id. Mirrors the index above
-- for the field that now carries the recovery-query load.
create index if not exists idx_episodes_payload_cwd
  on episodes ((payload->>'cwd'))
  where (payload->>'cwd') is not null;

alter table episodes enable row level security;

create policy "Allow all for authenticated" on episodes
  for all using (true) with check (true);

-- Anon access — INSERT/UPDATE/DELETE gated on `actor` (the column already
-- used as the provenance field per the convention comment above).
-- Slices 3 + 3.5, #542 + #565.
create policy "Anon select" on episodes
  for select to anon using (true);
create policy "Anon sandcastle update" on episodes
  for update to anon
  using (actor like 'sandcastle:%')
  with check (actor like 'sandcastle:%');
create policy "Anon sandcastle delete" on episodes
  for delete to anon
  using (actor like 'sandcastle:%');
create policy "Anon sandcastle insert" on episodes
  for insert to anon
  with check (actor like 'sandcastle:%');


-- =========================================================================
-- Phase 1 cont'd (memory overhaul, Osasuwu/jarvis#185) — supersedes-chain
-- collapse in link expansion.
--
-- Problem: match_memories and keyword_search_memories already filter out
-- superseded rows on the default path (show_history=false). But
-- get_linked_memories walks memory_links directly, which still points at
-- the older versions of a chain. So recall with include_links=true can
-- surface a memory whose current head was deliberately hidden by the
-- lifecycle filter — re-introducing the bug Phase 1 was meant to fix.
--
-- Fix:
--   1. find_chain_head(uuid, int) — recursive CTE walking superseded_by
--      to the terminal node. Returns the input id if it's already a head,
--      or the deepest reachable descendant (capped at max_depth to guard
--      against cycles from manual edits).
--   2. get_linked_memories replaced to (a) resolve the neighbor id through
--      find_chain_head before joining memories, (b) apply the same
--      lifecycle filter the other recall RPCs use (show_history param),
--      and (c) return content_updated_at + last_accessed_at so the caller
--      can temporally rescore linked memories consistently with primaries.
-- =========================================================================

-- Touch-safe update_updated_at: don't bump updated_at when the only column
-- changed is last_accessed_at (i.e. touch_memories RPC on recall). The
-- schema-level comment under Phase 0 already flagged this ("last_accessed_at
-- set by touch_memories only") but the generic trigger was rippling every
-- touch into updated_at, making session-start reads look like edits.
--
-- Why this matters: the fallback _keyword_recall orders by updated_at, and
-- session-context.py selects user memories by updated_at — neither should
-- be perturbed by the access-frequency touch.
--
-- Implementation: cheap short-circuit first — if last_accessed_at isn't
-- changing, this is a normal edit and updated_at must bump. Only on
-- touch-shaped UPDATEs do we pay for a JSONB diff of old vs new. This keeps
-- the hot path (regular writes) free of to_jsonb cost on the full row
-- (content can be kilobytes).
--
-- The JSONB diff strips last_accessed_at + updated_at (touch cols) and the
-- generated cols (fts, project_key), which appear NULL in NEW under BEFORE
-- UPDATE while OLD has the stored value (PostgreSQL quirk for GENERATED
-- ALWAYS STORED), so including them would always register as a diff. If
-- new generated columns are added, list them here too.
create or replace function update_updated_at()
returns trigger as $$
begin
  if new.last_accessed_at is distinct from old.last_accessed_at
     and (to_jsonb(new) - 'last_accessed_at' - 'updated_at' - 'fts' - 'project_key')
           = (to_jsonb(old) - 'last_accessed_at' - 'updated_at' - 'fts' - 'project_key') then
    return new;
  end if;
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;


create or replace function find_chain_head(mid uuid, max_depth int default 20)
returns uuid
language sql stable as $$
    with recursive chain as (
        select m.id, m.superseded_by, 0 as depth
        from memories m
        where m.id = mid
        union all
        select m.id, m.superseded_by, c.depth + 1
        from chain c
        join memories m on m.id = c.superseded_by
        where c.superseded_by is not null
          and c.depth < max_depth
    )
    -- Terminal node: either superseded_by IS NULL (a true head) or we hit
    -- max_depth (chain too long or cyclic — return deepest we saw).
    select id from chain order by depth desc limit 1;
$$;

drop function if exists get_linked_memories(uuid[], text[]);
drop function if exists get_linked_memories(uuid[], text[], boolean);
create or replace function get_linked_memories(
    memory_ids uuid[],
    link_types text[] default null,
    show_history boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    link_type text, link_strength float, linked_from uuid
)
language sql stable as $$
    select * from (
        -- DISTINCT ON collapses multiple edges between the same pair of
        -- memories — keep the strongest one after chain resolution. Two
        -- different originals may resolve to the same head, so we dedup
        -- on the resolved id rather than the raw link target.
        select distinct on (sub.id)
            sub.id, sub.name, sub.type, sub.project, sub.description,
            sub.content, sub.tags,
            sub.updated_at, sub.content_updated_at, sub.last_accessed_at,
            sub.link_type, sub.link_strength, sub.linked_from
        from (
            -- Outgoing edges: source ∈ memory_ids, target resolved to head.
            select m.id, m.name, m.type, m.project, m.description, m.content,
                   m.tags, m.updated_at, m.content_updated_at, m.last_accessed_at,
                   l.link_type, l.strength as link_strength, l.source_id as linked_from
            from memory_links l
            join memories m on m.id = coalesce(
                case when show_history then l.target_id
                     else find_chain_head(l.target_id) end,
                l.target_id)
            where l.source_id = any(memory_ids)
              and not (m.id = any(memory_ids))
              and (link_types is null or l.link_type = any(link_types))
              and (show_history
                   or (m.expired_at is null
                       and m.superseded_by is null
                       and (m.valid_to is null or m.valid_to > now())))
            union all
            -- Incoming edges: target ∈ memory_ids, source resolved to head.
            select m.id, m.name, m.type, m.project, m.description, m.content,
                   m.tags, m.updated_at, m.content_updated_at, m.last_accessed_at,
                   l.link_type, l.strength as link_strength, l.target_id as linked_from
            from memory_links l
            join memories m on m.id = coalesce(
                case when show_history then l.source_id
                     else find_chain_head(l.source_id) end,
                l.source_id)
            where l.target_id = any(memory_ids)
              and not (m.id = any(memory_ids))
              and (link_types is null or l.link_type = any(link_types))
              and (show_history
                   or (m.expired_at is null
                       and m.superseded_by is null
                       and (m.valid_to is null or m.valid_to > now())))
        ) sub
        order by sub.id, sub.link_strength desc, sub.link_type, sub.linked_from
    ) deduped
    order by deduped.link_strength desc
    limit 10;
$$;


-- =========================================================================
-- Phase 5.1b-β (memory overhaul, Osasuwu/jarvis#185, #221) — consolidation
-- apply path.
--
-- Builds on 5.1b-α (#220): the Haiku planner emits per-cluster plans of kind
-- MERGE / SUPERSEDE / KEEP_DISTINCT. 5.1b-β converts those plans into
-- actual mutations, gated by confidence (>= 0.85, owner-decided 2026-04-19).
-- Everything below the gate — and every KEEP_DISTINCT — is recorded in
-- memory_review_queue so we (a) have an audit trail and (b) don't re-spend
-- Haiku tokens on the same cluster every week.
--
-- Queue strategy (owner-decided: reuse, don't fork):
--   - Extend memory_review_queue.decision CHECK with MERGE,
--     SUPERSEDE_CONSOLIDATION, KEEP_DISTINCT. Phase 2 rows keep their ADD/
--     UPDATE/DELETE/NOOP semantics untouched.
--   - New column consolidation_payload jsonb holds cluster-level state
--     (member_ids, canonical_* fields, haiku metadata). Phase 2 rows leave
--     it NULL; a functional index makes "have I seen this cluster?" checks
--     cheap for the planner's pre-filter.
--   - Extend status CHECK with 'rolled_back' so a reverted entry is visibly
--     distinct from owner-rejected entries (the former means "try again
--     later", the latter means "never suggest again").
-- =========================================================================

alter table memory_review_queue
  drop constraint if exists memory_review_queue_decision_check;

alter table memory_review_queue
  add constraint memory_review_queue_decision_check
  check (decision in (
    'ADD', 'UPDATE', 'DELETE', 'NOOP',
    'MERGE', 'SUPERSEDE_CONSOLIDATION', 'KEEP_DISTINCT'
  ));

alter table memory_review_queue
  drop constraint if exists memory_review_queue_status_check;

alter table memory_review_queue
  add constraint memory_review_queue_status_check
  check (status in (
    'pending', 'approved', 'rejected', 'auto_applied', 'rolled_back'
  ));

alter table memory_review_queue
  add column if not exists consolidation_payload jsonb;

-- Functional index on the sorted-member-id key. The planner pre-filter asks
-- "does any queue row already cover this exact set of members?"; a direct
-- equality lookup on member_ids_key inside the jsonb is O(log n) with this
-- index and scales independent of Phase 2 row count.
create index if not exists idx_review_queue_member_ids_key
  on memory_review_queue((consolidation_payload->>'member_ids_key'))
  where consolidation_payload is not null;


-- apply_consolidation_plan: atomic per cluster.
--
-- MERGE: synthesize a new canonical memory from Haiku's output, mark every
-- member superseded_by the new id, insert a `consolidates` link per member.
-- Embedding is populated out-of-band by the caller (Python has VoyageAI);
-- the write leaves `embedding IS NULL` temporarily — acceptable since the
-- lifecycle filter on members immediately hides them from recall, and the
-- caller backfills within a second.
--
-- SUPERSEDE_CONSOLIDATION: one existing member wins, all others get
-- superseded_by = canonical. No new memory, no new embedding needed.
--
-- queue_meta (optional): when non-null, also inserts a memory_review_queue
-- row in the same transaction. This is the auto_applied audit trail and is
-- required for rollback — without it the mutation would be irreversible.
-- Keeping it in-RPC means a queue-insert failure rolls the mutation back
-- rather than leaving orphaned memory state (#224 fix).
--
-- Returns jsonb: { status, decision, canonical_id, superseded_count, queue_id? }.
--
-- Signature changed from 1-arg to 2-arg-with-default in #224. Postgres will
-- keep both variants installed side-by-side unless the old one is explicitly
-- dropped, and 1-arg callers will continue hitting the pre-#224 body. Mirrors
-- the `get_linked_memories` migration pattern.
drop function if exists apply_consolidation_plan(jsonb);
create or replace function apply_consolidation_plan(
    plan jsonb,
    queue_meta jsonb default null
)
returns jsonb
language plpgsql volatile
as $$
declare
    v_decision text := plan->>'decision';
    v_canonical_id uuid := nullif(plan->>'canonical_id', '')::uuid;
    v_supersede_ids uuid[] := array(
        select (jsonb_array_elements_text(coalesce(plan->'supersede_ids', '[]'::jsonb)))::uuid
    );
    v_tags text[] := array(
        select jsonb_array_elements_text(coalesce(plan->'canonical_tags', '[]'::jsonb))
    );
    v_new_id uuid;
    v_rows int;
    v_queue_id uuid;
    v_result jsonb;
    v_queue_target uuid;
begin
    if v_decision = 'MERGE' then
        if coalesce(plan->>'canonical_name', '') = ''
           or coalesce(plan->>'canonical_content', '') = ''
           or coalesce(plan->>'canonical_type', '') = ''
           or coalesce(plan->>'source_provenance', '') = '' then
            raise exception 'MERGE plan missing required canonical_* / source_provenance fields';
        end if;

        insert into memories (
            project, name, type, description, content, tags,
            source_provenance, confidence
        )
        values (
            nullif(plan->>'canonical_project', ''),
            plan->>'canonical_name',
            plan->>'canonical_type',
            nullif(plan->>'canonical_description', ''),
            plan->>'canonical_content',
            v_tags,
            plan->>'source_provenance',
            least(coalesce((plan->>'confidence')::float, 0.8), 0.9)
        )
        returning id into v_new_id;

        -- Supersede every member. Set lifecycle timestamps unconditionally
        -- (not coalesce) so rollback (which NULLs them) is symmetric. Members
        -- come from find_consolidation_clusters, which guarantees they're
        -- live (expired_at IS NULL, superseded_by IS NULL, deleted_at IS NULL,
        -- valid_to IS NULL OR future). A future valid_to is overwritten by
        -- now() — that future lifecycle plan is superseded by the consolidation,
        -- and rollback restores to NULL rather than trying to reconstruct it
        -- (documented behaviour; owner can re-set manually if needed).
        update memories
        set superseded_by = v_new_id,
            valid_to = now(),
            expired_at = now()
        where id = any(v_supersede_ids);
        get diagnostics v_rows = row_count;

        -- Insert `consolidates` links (canonical → each member). Rollback
        -- drops them. ON CONFLICT is a safety net against a retried apply;
        -- normal flow hits clean (source_id, target_id, link_type) tuples.
        insert into memory_links (source_id, target_id, link_type, strength)
        select v_new_id, unnest_id, 'consolidates', 1.0
        from unnest(v_supersede_ids) as unnest_id
        on conflict (source_id, target_id, link_type) do nothing;

        v_result := jsonb_build_object(
            'status', 'applied',
            'decision', 'MERGE',
            'canonical_id', v_new_id,
            'superseded_count', v_rows
        );
        v_queue_target := v_new_id;
    elsif v_decision = 'SUPERSEDE_CONSOLIDATION' then
        if v_canonical_id is null then
            raise exception 'SUPERSEDE_CONSOLIDATION requires canonical_id';
        end if;

        -- Same lifecycle semantics as MERGE: unconditional now() for
        -- round-trip symmetry with rollback.
        update memories
        set superseded_by = v_canonical_id,
            valid_to = now(),
            expired_at = now()
        where id = any(v_supersede_ids)
          and id <> v_canonical_id;
        get diagnostics v_rows = row_count;

        insert into memory_links (source_id, target_id, link_type, strength)
        select v_canonical_id, unnest_id, 'supersedes', 1.0
        from unnest(v_supersede_ids) as unnest_id
        where unnest_id <> v_canonical_id
        on conflict (source_id, target_id, link_type) do nothing;

        v_result := jsonb_build_object(
            'status', 'applied',
            'decision', 'SUPERSEDE_CONSOLIDATION',
            'canonical_id', v_canonical_id,
            'superseded_count', v_rows
        );
        v_queue_target := v_canonical_id;
    else
        raise exception 'Unsupported decision for apply_consolidation_plan: %', v_decision;
    end if;

    -- Queue audit row, transactional with the memory mutations. The RPC owns
    -- the load-bearing fields so a misbuilt caller payload can't corrupt the
    -- audit trail:
    --   * decision: always v_decision (validated above). If queue_meta carries
    --     a different decision, raise — silently overwriting hides a bug.
    --   * classifier_model: required + non-blank. The audit trail is the only
    --     place the model attribution survives; a NULL/empty write would lose
    --     it silently and break provenance tracing.
    --   * target_id: the canonical the RPC just computed (v_new_id for MERGE,
    --     v_canonical_id for SUPERSEDE) — rollback_consolidation reads it and
    --     raises on NULL, so owning the assignment here avoids the caller
    --     having to echo it back.
    if queue_meta is not null then
        if queue_meta ? 'decision'
           and queue_meta->>'decision' is not null
           and queue_meta->>'decision' <> v_decision then
            raise exception
                'queue_meta decision % does not match plan decision %',
                queue_meta->>'decision', v_decision;
        end if;

        if coalesce(nullif(trim(queue_meta->>'classifier_model'), ''), '') = '' then
            raise exception
                'queue_meta.classifier_model is required and must be non-blank for audit provenance';
        end if;

        insert into memory_review_queue (
            decision,
            confidence,
            reasoning,
            status,
            consolidation_payload,
            classifier_model,
            target_id,
            applied_at
        )
        values (
            v_decision,
            coalesce((queue_meta->>'confidence')::float, 0.0),
            left(coalesce(queue_meta->>'reasoning', ''), 1000),
            coalesce(queue_meta->>'status', 'auto_applied'),
            queue_meta->'consolidation_payload',
            trim(queue_meta->>'classifier_model'),
            v_queue_target,
            coalesce(
                nullif(queue_meta->>'applied_at', '')::timestamptz,
                now()
            )
        )
        returning id into v_queue_id;

        v_result := v_result || jsonb_build_object('queue_id', v_queue_id);
    end if;

    return v_result;
end;
$$;


-- rollback_consolidation(queue_id): inverse of apply, keyed by the queue
-- entry so the member-id list is authoritative (not re-derived from links,
-- which could collide with Phase 2 classifier supersedes).
--
-- MERGE rollback:  soft-delete the synthesized canonical, clear members'
--                  lifecycle cols, remove `consolidates` links.
-- SUPERSEDE_CONSOLIDATION rollback: leave canonical live, restore the
--                  losers, remove `supersedes` links created by this apply.
--
-- Status transitions: 'auto_applied'/'approved' → 'rolled_back'. Rejecting
-- an already-applied entry is not supported through this RPC — it would
-- conflate "owner disapproves future suggestion" with "undo this mutation".
create or replace function rollback_consolidation(queue_id uuid)
returns jsonb
language plpgsql volatile
as $$
declare
    v_payload jsonb;
    v_decision text;
    v_status text;
    v_canonical_id uuid;
    v_supersede_ids uuid[];
    v_rows int;
begin
    select consolidation_payload, decision, status, target_id
    into v_payload, v_decision, v_status, v_canonical_id
    from memory_review_queue
    where id = queue_id
    for update;

    if v_payload is null then
        raise exception 'Queue entry % missing (or has no consolidation_payload)', queue_id;
    end if;

    if v_decision not in ('MERGE', 'SUPERSEDE_CONSOLIDATION') then
        raise exception 'Entry % is not a consolidation row (decision=%)', queue_id, v_decision;
    end if;

    if v_status not in ('auto_applied', 'approved') then
        raise exception 'Can only roll back auto_applied/approved entries (status=%)', v_status;
    end if;

    if v_canonical_id is null then
        raise exception 'Entry % has no target_id to roll back', queue_id;
    end if;

    v_supersede_ids := array(
        select (jsonb_array_elements_text(coalesce(v_payload->'supersede_ids', '[]'::jsonb)))::uuid
    );

    update memories
    set superseded_by = null,
        valid_to = null,
        expired_at = null
    where id = any(v_supersede_ids)
      and superseded_by = v_canonical_id;
    get diagnostics v_rows = row_count;

    delete from memory_links
    where source_id = v_canonical_id
      and target_id = any(v_supersede_ids)
      and link_type in ('consolidates', 'supersedes');

    if v_decision = 'MERGE' then
        update memories
        set deleted_at = now()
        where id = v_canonical_id;
    end if;

    update memory_review_queue
    set status = 'rolled_back',
        reviewed_at = now(),
        reviewed_by = coalesce(reviewed_by, 'rollback_script')
    where id = queue_id;

    return jsonb_build_object(
        'status', 'rolled_back',
        'decision', v_decision,
        'canonical_id', v_canonical_id,
        'canonical_soft_deleted', v_decision = 'MERGE',
        'restored_count', v_rows
    );
end;
$$;


-- approve_consolidation(queue_id, reviewer): owner-review approval path for
-- Phase 5.1d-β (#226). Reads the stored consolidation_payload, rebuilds the
-- plan, calls apply_consolidation_plan in the same transaction (SELECT FOR
-- UPDATE against concurrent approves), and transitions the queue row to
-- status=approved. The queue_meta arg is intentionally omitted — we're
-- updating an existing row, not inserting a new one.
--
-- source_provenance on the synthesized canonical is stamped
-- `cli:review:<date>` so audit traces distinguish CLI-approved merges from
-- auto_applied ones. canonical embedding is backfilled out-of-band by the
-- Python caller (VoyageAI); the row is live-hidden until embedding lands
-- because the member lifecycle filter already masks it from recall.
--
-- Race: two concurrent approves of the same row both take FOR UPDATE on the
-- queue row, serialize, and the second one sees status='approved' and raises.
create or replace function approve_consolidation(
    queue_id uuid,
    reviewer text default 'cli_review'
)
returns jsonb
language plpgsql volatile
as $$
declare
    v_decision text;
    v_status text;
    v_payload jsonb;
    v_existing_target uuid;
    v_plan jsonb;
    v_applied jsonb;
    v_canonical_id uuid;
    v_today text := to_char(now() at time zone 'UTC', 'YYYY-MM-DD');
begin
    select decision, status, consolidation_payload, target_id
    into v_decision, v_status, v_payload, v_existing_target
    from memory_review_queue
    where id = queue_id
    for update;

    if not found then
        raise exception 'Queue entry % not found', queue_id;
    end if;

    if v_payload is null then
        raise exception 'Queue entry % has no consolidation_payload', queue_id;
    end if;

    if v_status <> 'pending' then
        raise exception 'Can only approve pending entries (status=%)', v_status;
    end if;

    if v_decision not in ('MERGE', 'SUPERSEDE_CONSOLIDATION') then
        raise exception 'Entry % is not a consolidation row (decision=%)', queue_id, v_decision;
    end if;

    -- Rebuild the plan from the stored payload. source_provenance gets
    -- overwritten to reflect reviewer provenance (cli:review:<today>) rather
    -- than the original skill:consolidation:... stamp from the planner.
    v_plan := jsonb_build_object(
        'decision', v_decision,
        'supersede_ids', coalesce(v_payload->'supersede_ids', '[]'::jsonb),
        'source_provenance', 'cli:review:' || v_today,
        'confidence', coalesce((v_payload->>'haiku_confidence')::float, 0.85)
    );

    if v_decision = 'MERGE' then
        v_plan := v_plan || jsonb_build_object(
            'canonical_name', v_payload->>'canonical_name',
            'canonical_type', v_payload->>'canonical_type',
            'canonical_description', v_payload->>'canonical_description',
            'canonical_content', v_payload->>'canonical_content',
            'canonical_project', v_payload->>'canonical_project',
            'canonical_tags', coalesce(v_payload->'canonical_tags', '[]'::jsonb)
        );
    else
        -- SUPERSEDE_CONSOLIDATION: canonical_id lives in target_id on the
        -- pending queue row (set by queue_for_review). Fall back to payload
        -- if the caller didn't populate target_id historically.
        v_canonical_id := v_existing_target;
        if v_canonical_id is null then
            v_canonical_id := nullif(v_payload->>'canonical_id', '')::uuid;
        end if;
        if v_canonical_id is null then
            raise exception 'SUPERSEDE_CONSOLIDATION queue row % has no canonical_id (target_id + payload both null)', queue_id;
        end if;
        v_plan := v_plan || jsonb_build_object('canonical_id', v_canonical_id);
    end if;

    -- Apply. Don't pass queue_meta — we're updating this existing row, not
    -- creating a new audit entry.
    v_applied := apply_consolidation_plan(v_plan);
    v_canonical_id := (v_applied->>'canonical_id')::uuid;

    update memory_review_queue
    set status = 'approved',
        reviewed_at = now(),
        reviewed_by = reviewer,
        applied_at = now(),
        target_id = v_canonical_id
    where id = queue_id;

    return v_applied || jsonb_build_object(
        'queue_id', queue_id,
        'approved_by', reviewer
    );
end;
$$;


-- reject_consolidation(queue_id, reason, reviewer): mark a pending queue
-- entry rejected so it won't be replanned (see find_consolidation_clusters
-- dedup, which excludes 'rolled_back' but INCLUDES 'rejected'). Atomic —
-- SELECT FOR UPDATE + status validation + UPDATE happen in one transaction.
-- Two concurrent rejects of the same row serialize; the second sees
-- status='rejected' and raises.
--
-- The reason (when provided) is appended to the reasoning column so the
-- CLI `--list` view shows why it was rejected, without losing the original
-- Haiku reasoning.
create or replace function reject_consolidation(
    queue_id uuid,
    reason text default null,
    reviewer text default 'cli_review'
)
returns jsonb
language plpgsql volatile
as $$
declare
    v_decision text;
    v_status text;
    v_reasoning text;
    v_new_reasoning text;
begin
    select decision, status, reasoning
    into v_decision, v_status, v_reasoning
    from memory_review_queue
    where id = queue_id
    for update;

    if not found then
        raise exception 'Queue entry % not found', queue_id;
    end if;

    if v_status <> 'pending' then
        raise exception 'Can only reject pending entries (status=%)', v_status;
    end if;

    v_new_reasoning := coalesce(v_reasoning, '');
    if reason is not null and trim(reason) <> '' then
        v_new_reasoning := left(
            v_new_reasoning || E'\n-- rejected: ' || trim(reason),
            1000
        );
    end if;

    update memory_review_queue
    set status = 'rejected',
        reviewed_at = now(),
        reviewed_by = reviewer,
        reasoning = v_new_reasoning
    where id = queue_id;

    return jsonb_build_object(
        'status', 'rejected',
        'decision', v_decision,
        'queue_id', queue_id,
        'rejected_by', reviewer,
        'reason', reason
    );
end;
$$;


-- =========================================================================
-- Phase 5.2-β (memory overhaul, Osasuwu/jarvis#185, #232) — A-MEM neighbor
-- evolution apply path.
--
-- Builds on 5.2-α (#230, #231): the Haiku planner surfaces per-neighbor
-- tag/description evolution proposals for each recent UPDATE decision.
-- 5.2-β converts those plans into actual mutations of the neighbor rows,
-- gated by the same 0.85 confidence threshold used by consolidation.
-- Rejected/low-confidence plans land in memory_review_queue for later
-- owner review; high-confidence ones auto-apply with a full rollback
-- snapshot so any mistake is reversible.
--
-- Queue strategy (reuse consolidation pattern):
--   - Extend memory_review_queue.decision CHECK with 'EVOLVE'. Phase 2
--     and 5.1b rows keep their decision semantics untouched.
--   - New column evolution_payload jsonb holds per-neighbor snapshots
--     (neighbor_id, old_tags, old_description, new_tags, new_description,
--      action, confidence, reasoning) + parent lineage (update_queue_id,
--      candidate_id, target_id). consolidation_payload stays NULL.
--   - status CHECK is unchanged — already includes 'rolled_back' from
--     5.1b-β.
--
-- target_id semantics for EVOLVE: the candidate_id of the spawning UPDATE
-- (i.e. the memory whose arrival triggered the neighbor refresh). Neighbors
-- themselves are listed inside evolution_payload.snapshots[].neighbor_id.
-- =========================================================================

alter table memory_review_queue
  drop constraint if exists memory_review_queue_decision_check;

alter table memory_review_queue
  add constraint memory_review_queue_decision_check
  check (decision in (
    'ADD', 'UPDATE', 'DELETE', 'NOOP',
    'MERGE', 'SUPERSEDE_CONSOLIDATION', 'KEEP_DISTINCT',
    'EVOLVE'
  ));

alter table memory_review_queue
  add column if not exists evolution_payload jsonb;

-- Index for "has this UPDATE already been evolved?" dedup in the planner.
-- The 5.2-α script's `fetch_recent_updates` pre-filter can use this to
-- skip UPDATEs that already have a queue EVOLVE row (pending or applied).
create index if not exists idx_review_queue_update_queue_id
  on memory_review_queue((evolution_payload->>'update_queue_id'))
  where evolution_payload is not null;


-- apply_evolution_plan: atomic per plan.
--
-- For each non-KEEP proposal:
--   1. SELECT current (tags, description, content_updated_at) FOR UPDATE —
--      this is the snapshot that `rollback_evolution` restores from.
--   2. UPDATE memories SET tags = new_tags (if present), description =
--      new_description (if present). The content_updated_at trigger fires
--      and bumps it to now() — intentional, this IS a content change.
--
-- KEEP proposals are filtered by the caller; if any sneak through, the RPC
-- skips them (they'd be no-ops anyway, but surfacing them in the snapshot
-- would pollute the rollback payload).
--
-- queue_meta (optional, mirrors apply_consolidation_plan): when non-null,
-- inserts a memory_review_queue row in the same transaction for audit +
-- rollback. Required for rollback — a mutation with no queue row is
-- irrecoverable.
--
-- Returns jsonb: { status, decision, applied_count, queue_id? }.
create or replace function apply_evolution_plan(
    plan jsonb,
    queue_meta jsonb default null
)
returns jsonb
language plpgsql volatile
as $$
declare
    v_decision text := plan->>'decision';
    v_proposals jsonb := coalesce(plan->'proposals', '[]'::jsonb);
    v_proposal jsonb;
    v_snapshots jsonb := '[]'::jsonb;
    v_applied_count int := 0;
    v_neighbor_id uuid;
    v_action text;
    v_old_tags text[];
    v_old_desc text;
    v_old_content_updated_at timestamptz;
    v_new_tags text[];
    v_new_desc text;
    v_queue_id uuid;
    v_result jsonb;
    v_evolution_payload jsonb;
begin
    if v_decision <> 'EVOLVE' then
        raise exception 'apply_evolution_plan only handles EVOLVE (got %)', v_decision;
    end if;

    if coalesce(plan->>'source_provenance', '') = '' then
        raise exception 'EVOLVE plan missing required source_provenance';
    end if;

    -- Mutate neighbors + record snapshots in a single pass.
    for v_proposal in select * from jsonb_array_elements(v_proposals)
    loop
        v_action := v_proposal->>'action';
        if v_action not in ('UPDATE_TAGS', 'UPDATE_DESC', 'UPDATE_BOTH') then
            -- KEEP or anything unrecognized — skip, don't snapshot.
            continue;
        end if;

        v_neighbor_id := (v_proposal->>'neighbor_id')::uuid;

        select tags, description, content_updated_at
        into v_old_tags, v_old_desc, v_old_content_updated_at
        from memories
        where id = v_neighbor_id
        for update;

        if not found then
            -- Neighbor vanished between planning and apply. Skip — don't
            -- fail the whole plan. Log via snapshot with a missing flag
            -- so owner-review UIs can surface the skip.
            v_snapshots := v_snapshots || jsonb_build_object(
                'neighbor_id', v_neighbor_id,
                'skipped', 'neighbor_missing'
            );
            continue;
        end if;

        v_new_tags := null;
        v_new_desc := null;
        if v_action in ('UPDATE_TAGS', 'UPDATE_BOTH') then
            -- Contract: action committed to a tag change, so the payload
            -- MUST carry a non-null new_tags array. Empty-array payloads
            -- pass (explicit "clear tags" is a valid evolution), but
            -- missing/null would silently wipe the neighbor's tags under
            -- the old coalesce(..., '[]') behaviour — raise instead so the
            -- caller surfaces the planner bug.
            if not (v_proposal ? 'new_tags')
               or jsonb_typeof(v_proposal->'new_tags') = 'null' then
                raise exception
                    'EVOLVE action % requires non-null new_tags for neighbor %',
                    v_action, v_neighbor_id;
            end if;
            v_new_tags := array(
                select jsonb_array_elements_text(v_proposal->'new_tags')
            );
        end if;
        if v_action in ('UPDATE_DESC', 'UPDATE_BOTH') then
            if not (v_proposal ? 'new_description')
               or jsonb_typeof(v_proposal->'new_description') = 'null' then
                raise exception
                    'EVOLVE action % requires non-null new_description for neighbor %',
                    v_action, v_neighbor_id;
            end if;
            v_new_desc := v_proposal->>'new_description';
        end if;

        update memories
        set tags = coalesce(v_new_tags, tags),
            description = coalesce(v_new_desc, description)
        where id = v_neighbor_id;

        v_applied_count := v_applied_count + 1;

        v_snapshots := v_snapshots || jsonb_build_object(
            'neighbor_id', v_neighbor_id,
            'action', v_action,
            -- Store old_tags as a JSON array even when memories.tags was NULL.
            -- to_jsonb(NULL::text[]) produces JSONB 'null' (not SQL NULL),
            -- which would later trip jsonb_array_elements_text in rollback.
            -- Normalize to '[]' at write time.
            'old_tags', coalesce(to_jsonb(v_old_tags), '[]'::jsonb),
            'old_description', v_old_desc,
            'old_content_updated_at', v_old_content_updated_at,
            'new_tags', to_jsonb(v_new_tags),
            'new_description', v_new_desc,
            'confidence', v_proposal->'confidence',
            'reasoning', v_proposal->>'reasoning'
        );
    end loop;

    v_evolution_payload := jsonb_build_object(
        'update_queue_id', plan->>'update_queue_id',
        'candidate_id', plan->>'candidate_id',
        'target_id', plan->>'target_id',
        'source_provenance', plan->>'source_provenance',
        'snapshots', v_snapshots
    );

    v_result := jsonb_build_object(
        'status', 'applied',
        'decision', 'EVOLVE',
        'applied_count', v_applied_count
    );

    -- Queue audit row, transactional with the memory mutations. Same
    -- load-bearing-field ownership pattern as apply_consolidation_plan:
    -- decision and classifier_model must match + be non-blank.
    if queue_meta is not null then
        if queue_meta ? 'decision'
           and queue_meta->>'decision' is not null
           and queue_meta->>'decision' <> 'EVOLVE' then
            raise exception
                'queue_meta decision % does not match plan decision EVOLVE',
                queue_meta->>'decision';
        end if;

        if coalesce(nullif(trim(queue_meta->>'classifier_model'), ''), '') = '' then
            raise exception
                'queue_meta.classifier_model is required and must be non-blank for audit provenance';
        end if;

        insert into memory_review_queue (
            decision,
            confidence,
            reasoning,
            status,
            evolution_payload,
            classifier_model,
            target_id,
            applied_at
        )
        values (
            'EVOLVE',
            coalesce((queue_meta->>'confidence')::float, 0.0),
            left(coalesce(queue_meta->>'reasoning', ''), 1000),
            coalesce(queue_meta->>'status', 'auto_applied'),
            v_evolution_payload,
            trim(queue_meta->>'classifier_model'),
            nullif(plan->>'candidate_id', '')::uuid,
            coalesce(
                nullif(queue_meta->>'applied_at', '')::timestamptz,
                now()
            )
        )
        returning id into v_queue_id;

        v_result := v_result || jsonb_build_object('queue_id', v_queue_id);
    end if;

    return v_result;
end;
$$;


-- rollback_evolution(queue_id): restore neighbors from the snapshot stored
-- in evolution_payload.snapshots[].
--
-- content_updated_at: the update_content_updated_at trigger will fire on
-- the rollback UPDATE and bump content_updated_at to now(). This IS a
-- departure from 5.1b-β rollback's byte-identical restoration — the 5.1b-β
-- path only touches lifecycle columns (superseded_by, valid_to,
-- expired_at) which aren't tracked by the content trigger, while 5.2
-- rollback has to touch tags/description which are tracked. Accepted
-- tradeoff: rollback is rare, content_updated_at is a soft decay signal,
-- and a rolled-back neighbor WAS recently touched (by the failed evolve).
--
-- Status transitions: 'auto_applied'/'approved' → 'rolled_back'.
create or replace function rollback_evolution(queue_id uuid)
returns jsonb
language plpgsql volatile
as $$
declare
    v_payload jsonb;
    v_decision text;
    v_status text;
    v_snapshots jsonb;
    v_snapshot jsonb;
    v_restored_count int := 0;
begin
    select evolution_payload, decision, status
    into v_payload, v_decision, v_status
    from memory_review_queue
    where id = queue_id
    for update;

    if v_payload is null then
        raise exception 'Queue entry % missing (or has no evolution_payload)', queue_id;
    end if;

    if v_decision <> 'EVOLVE' then
        raise exception 'Entry % is not an evolution row (decision=%)', queue_id, v_decision;
    end if;

    if v_status not in ('auto_applied', 'approved') then
        raise exception 'Can only roll back auto_applied/approved entries (status=%)', v_status;
    end if;

    v_snapshots := coalesce(v_payload->'snapshots', '[]'::jsonb);

    for v_snapshot in select * from jsonb_array_elements(v_snapshots)
    loop
        -- Skip snapshots that recorded a missing neighbor at apply time —
        -- nothing to restore.
        if v_snapshot ? 'skipped' then
            continue;
        end if;

        update memories
        set tags = array(
                select jsonb_array_elements_text(
                    -- Defend against legacy rows where old_tags was stored
                    -- as JSONB 'null' (pre-fix apply path): jsonb_array_elements_text
                    -- would raise on a non-array scalar. New rows are already
                    -- normalized at write time, but this keeps rollback safe
                    -- for rows inserted by the un-patched apply RPC.
                    case
                        when v_snapshot->'old_tags' is null
                             or jsonb_typeof(v_snapshot->'old_tags') <> 'array'
                        then '[]'::jsonb
                        else v_snapshot->'old_tags'
                    end
                )
            ),
            description = v_snapshot->>'old_description'
        where id = (v_snapshot->>'neighbor_id')::uuid;

        if found then
            v_restored_count := v_restored_count + 1;
        end if;
    end loop;

    update memory_review_queue
    set status = 'rolled_back',
        reviewed_at = now(),
        reviewed_by = coalesce(reviewed_by, 'rollback_script')
    where id = queue_id;

    return jsonb_build_object(
        'status', 'rolled_back',
        'decision', 'EVOLVE',
        'restored_count', v_restored_count
    );
end;
$$;


-- =========================================================================
-- #242: dual-embedding read-path for voyage model migration readiness
--
-- Adds a second, independently-indexed embedding column so we can dual-write
-- today (zero risk) and cut over later without a hot-rebuild. Keeping the
-- two columns + two indexes lets each RPC use its own HNSW index — no CASE
-- in ORDER BY (which silently drops to seq-scan and killed PR #245).
--
-- Dimensions: current voyage-3-lite is 512, voyage-3 is 1024. v2 is sized
-- for the next model we'd migrate to. If we ever bump to a different model
-- family (e.g. BGE-Large = 1024, OpenAI text-embedding-3-large = 3072),
-- add a further column / RPC rather than resizing.
--
-- No data migration here. Backfill is a separate issue.
-- =========================================================================

alter table memories add column if not exists embedding_v2 vector(1024);
alter table memories add column if not exists embedding_model_v2 text;
alter table memories add column if not exists embedding_version_v2 text;

-- Partial HNSW index — only rows with populated v2 get indexed. Keeps the
-- structure small while SECONDARY is unset (zero rows indexed initially).
create index if not exists idx_memories_embedding_v2_hnsw
  on memories using hnsw (embedding_v2 vector_cosine_ops)
  with (m = 16, ef_construction = 64)
  where embedding_v2 is not null;

-- Read-path RPC for v2. Mirrors match_memories shape/filters so _hybrid_recall
-- can swap by name. NO CASE expression in ORDER BY — pgvector HNSW requires
-- a direct column reference to use the index.
drop function if exists match_memories_v2(vector, int, float, text, text, boolean);
drop function if exists match_memories_v2(vector, int, float, text, text, boolean, boolean);
create or replace function match_memories_v2(
    query_embedding vector,
    match_limit int default 10,
    similarity_threshold float default 0.3,
    filter_project text default null,
    filter_type text default null,
    show_history boolean default false,
    include_unreviewed boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    -- requires_review surfaced so Python-side temporal scoring can floor
    -- entrenchment for unreviewed candidates (see apply_temporal_scoring).
    requires_review boolean,
    similarity float
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.content_updated_at, m.last_accessed_at,
           m.requires_review,
           1 - (m.embedding_v2 <=> query_embedding) as similarity
    from memories m
    where m.embedding_v2 is not null
      and m.deleted_at is null
      and 1 - (m.embedding_v2 <=> query_embedding) >= similarity_threshold
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
      and (show_history
           or (m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
      -- Deriver review-gate (issue #552): see match_memories.
      and (include_unreviewed or m.requires_review = false)
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by m.embedding_v2 <=> query_embedding
    limit match_limit;
$$;

-- =========================================================================
-- Known unknowns: retrieval gaps + unsatisfied queries
-- Phase 5.3 — detect queries that consistently fail to match memories
--
-- Embedding dimension matches the PRIMARY embedding model (voyage-3-lite,
-- 512 dims). If EMBEDDING_MODEL_PRIMARY is switched to voyage-3 (1024
-- dims), this table needs a parallel column like memories.embedding_v2.
-- Until then, server-side gap upserts skip the embedding on dim mismatch
-- rather than crashing silently (see _upsert_known_unknown).
-- =========================================================================

CREATE TABLE IF NOT EXISTS known_unknowns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query text NOT NULL,
  query_embedding vector(512),
  top_similarity float NOT NULL,
  top_memory_id uuid REFERENCES memories(id) ON DELETE SET NULL,
  context jsonb,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  hit_count int NOT NULL DEFAULT 1,
  resolved_at timestamptz,
  resolved_by_memory_id uuid REFERENCES memories(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed'))
);

-- Index covers the /status ORDER BY (hit_count DESC, last_seen_at DESC)
CREATE INDEX IF NOT EXISTS idx_known_unknowns_status_hit
  ON known_unknowns(status, hit_count DESC, last_seen_at DESC)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_known_unknowns_query_embedding_hnsw
  ON known_unknowns USING hnsw (query_embedding vector_cosine_ops)
  WHERE query_embedding IS NOT NULL;

ALTER TABLE known_unknowns ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for authenticated" ON known_unknowns
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for anon" ON known_unknowns
  FOR ALL TO anon USING (true) WITH CHECK (true);

-- =========================================================================
-- Phase 5 Metacognition: Confidence Calibration (#251)
--
-- Links task outcomes to the memories that informed them and exposes a
-- Brier-score summary so /reflect and /self-improve can detect systemic
-- over- or under-confidence per memory type.
-- =========================================================================

-- Link column: which memory most informed this outcome's decision.
-- Moved inline into the canonical `create table task_outcomes` block
-- above (#660 doc reconciliation) — this ALTER TABLE only mattered for
-- databases that ran this file before the column existed; retained here
-- as a no-op comment so the migration history stays legible rather than
-- silently vanishing. Do not reintroduce the ALTER TABLE statement.

create index if not exists idx_task_outcomes_memory_id
  on task_outcomes(memory_id) where memory_id is not null;

-- Per-memory calibration view: predicted confidence vs actual outcome rate.
-- Only includes memories with at least one resolved outcome (success or
-- failure). Partial/unknown/pending outcomes are excluded — they would
-- bias the Brier score either way.
create or replace view memory_calibration
  with (security_invoker = on) as
select
  m.id as memory_id,
  m.type as memory_type,
  m.name as memory_name,
  m.project,
  m.confidence as predicted_confidence,
  count(o.id) as outcome_count,
  sum(case when o.outcome_status = 'success' then 1 else 0 end) as success_count,
  sum(case when o.outcome_status = 'failure' then 1 else 0 end) as failure_count,
  -- actual_rate in [0, 1]: fraction of (success+failure) outcomes that succeeded
  (sum(case when o.outcome_status = 'success' then 1.0 else 0.0 end)
    / nullif(count(o.id), 0))::float as actual_rate,
  -- Brier contribution per memory: (predicted - actual)^2, bounded [0, 1]
  (power(
    coalesce(m.confidence, 0.5)
    - (sum(case when o.outcome_status = 'success' then 1.0 else 0.0 end)
       / nullif(count(o.id), 0)),
    2
  ))::float as brier
from memories m
join task_outcomes o
  on o.memory_id = m.id
 and o.outcome_status in ('success', 'failure')
where m.deleted_at is null
  and m.superseded_by is null
group by m.id, m.type, m.name, m.project, m.confidence;

-- Summary RPC: aggregate brier by type and flag over/under-confidence.
-- Returns a single-row table so Supabase client .data is a list with one
-- item — caller picks [0] (pattern matches other RPCs in this file).
--
-- A type is flagged overconfident when its mean brier > 0.25 AND mean
-- predicted_confidence > mean actual_rate (warning level bias). Threshold
-- 0.25 is the midpoint of a Brier score for a miscalibrated binary
-- classifier; tighter if we need less noise, looser if we need more.
create or replace function memory_calibration_summary(
  p_project text default null
)
returns table (
  overall_brier float,
  total_memories bigint,
  by_type jsonb,
  warnings jsonb
)
language sql stable
as $$
  with scoped as (
    select *
    from memory_calibration
    where p_project is null or project = p_project
  ),
  agg_by_type as (
    select
      memory_type,
      count(*)::bigint as n,
      avg(brier)::float as brier,
      avg(predicted_confidence)::float as avg_predicted,
      avg(actual_rate)::float as avg_actual
    from scoped
    group by memory_type
  ),
  type_rows as (
    select jsonb_agg(
      jsonb_build_object(
        'type', memory_type,
        'n', n,
        'brier', brier,
        'avg_predicted', avg_predicted,
        'avg_actual', avg_actual,
        'over_confident', (brier > 0.25 and avg_predicted > avg_actual),
        'under_confident', (brier > 0.25 and avg_predicted < avg_actual)
      )
      order by brier desc
    ) as data
    from agg_by_type
  ),
  warning_rows as (
    select jsonb_agg(
      format(
        '%s: brier=%s (%s)',
        memory_type,
        round(brier::numeric, 3),
        case when avg_predicted > avg_actual
             then 'overconfident' else 'underconfident' end
      )
    ) as data
    from agg_by_type
    where brier > 0.25
  )
  select
    coalesce((select avg(brier) from scoped), 0.0)::float as overall_brier,
    (select count(*) from scoped)::bigint as total_memories,
    coalesce((select data from type_rows), '[]'::jsonb) as by_type,
    coalesce((select data from warning_rows), '[]'::jsonb) as warnings;
$$;


-- =========================================================================
-- Phase 5.3-δ (issue #445): FOK calibration summary RPC
-- Computes Brier score (mean squared error) of FOK verdicts against task outcomes
-- for confidence calibration analysis.
--
-- verdict_score mapping: sufficient=1.0, partial=0.5, insufficient=0.0, unknown=NULL
-- outcome_score mapping: success=1.0, partial=0.5, failure=0.0, unknown=NULL
--   (task_outcomes.outcome_status enum values defined in schema.sql line ~335)
--
-- brier = mean((verdict_score - outcome_score)^2) over joined rows
-- drift_signal = true if (brier >= 0.25 AND n >= 30); false if n < 30
-- NULL scores excluded from n and brier computation.
-- =========================================================================

create or replace function fok_calibration_summary(p_project text default null)
returns table (
  n integer,
  brier numeric,
  by_verdict json,
  drift_signal boolean
)
language sql stable
as $$
with judgments_with_scores as (
  -- Map fok_judgments.verdict → numeric score
  select
    fj.id,
    fj.verdict,
    case fj.verdict
      when 'sufficient' then 1.0
      when 'partial' then 0.5
      when 'insufficient' then 0.0
      when 'unknown' then null
      when 'skipped' then null
      else null
    end as verdict_score,
    -- Map task_outcomes.outcome_status → numeric score
    case tout.outcome_status
      when 'success' then 1.0
      when 'partial' then 0.5
      when 'failure' then 0.0
      when 'unknown' then null
      when 'pending' then null
      else null
    end as outcome_score,
    fj.project
  from fok_judgments fj
  left join task_outcomes tout on fj.outcome_id = tout.id
  -- Per-project queries return ONLY that project's rows (no NULL-project bleed).
  where (p_project is null or fj.project = p_project)
),
filtered_joined as (
  -- Calibration math requires both verdict_score AND outcome_score.
  select verdict_score, outcome_score
  from judgments_with_scores
  where verdict_score is not null and outcome_score is not null
),
calibration_stats as (
  select
    count(*)::integer as total_count,
    avg(power(verdict_score - outcome_score, 2)) as brier_value
  from filtered_joined
)
select
  cs.total_count,
  round(cs.brier_value::numeric, 4),
  -- by_verdict counts judgments with an outcome per verdict label.
  -- Unknown/skipped have NULL verdict_score by definition; require only
  -- outcome_score for that bucket so it isn't silently always 0.
  json_build_object(
    'sufficient', (select count(*) from judgments_with_scores where verdict = 'sufficient' and verdict_score is not null and outcome_score is not null),
    'partial', (select count(*) from judgments_with_scores where verdict = 'partial' and verdict_score is not null and outcome_score is not null),
    'insufficient', (select count(*) from judgments_with_scores where verdict = 'insufficient' and verdict_score is not null and outcome_score is not null),
    'unknown', (select count(*) from judgments_with_scores where verdict in ('unknown', 'skipped') and outcome_score is not null)
  ),
  case
    when cs.total_count < 30 then false
    else (cs.brier_value >= 0.25)
  end as drift_signal
from calibration_stats cs;
$$;


-- =========================================================================
-- Recall soft-delete filter fix (Osasuwu/jarvis#284)
--
-- Problem: match_memories, keyword_search_memories, and get_linked_memories
-- filter expired_at / superseded_by / valid_to on the show_history=false
-- path but forget deleted_at, so memory_recall surfaces soft-deleted rows
-- that memory_get / memory_list / match_memories_v2 correctly hide. Align
-- every recall-path RPC with the rest of the API: deleted_at IS NULL is
-- always required when show_history=false.
--
-- CREATE OR REPLACE redefines each function in-place; no data migration
-- needed.
-- =========================================================================

drop function if exists match_memories(vector, int, float, text, text, boolean);
drop function if exists match_memories(vector, int, float, text, text, boolean, boolean);
create or replace function match_memories(
    query_embedding vector,
    match_limit int default 10,
    similarity_threshold float default 0.3,
    filter_project text default null,
    filter_type text default null,
    show_history boolean default false,
    include_unreviewed boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    requires_review boolean,
    similarity float
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.content_updated_at, m.last_accessed_at,
           m.requires_review,
           1 - (m.embedding <=> query_embedding) as similarity
    from memories m
    where m.embedding is not null
      -- deleted_at is null is always required — soft-deleted rows must
      -- never surface, even with show_history=true. Mirrors match_memories_v2
      -- (line 2113) to eliminate the cross-RPC asymmetry where
      -- show_history=true was surfacing soft-deleted rows here but not from
      -- the primary embedding slot.
      and m.deleted_at is null
      and 1 - (m.embedding <=> query_embedding) >= similarity_threshold
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
      and (show_history
           or (m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
      -- Deriver review-gate (issue #552): hide rows pending owner review
      -- and merge-proposal candidates from default recall regardless of
      -- show_history. These are not "history" — they are unverified writes
      -- that must not surface to other repos or consumers.
      --
      -- The `array_length(..., 1) = 0` branch is technically unreachable —
      -- the `memories_merge_targets_non_empty` CHECK forbids empty arrays
      -- and `array_length('{}'::uuid[], 1)` returns NULL, not 0. Kept as a
      -- belt-and-braces guard; cost is negligible and `is null` is the
      -- short-circuit path via the partial GIN index.
      and (include_unreviewed or m.requires_review = false)
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by m.embedding <=> query_embedding
    limit match_limit;
$$;

drop function if exists keyword_search_memories(text, int, text, text, boolean);
drop function if exists keyword_search_memories(text, int, text, text, boolean, boolean);
create or replace function keyword_search_memories(
    search_query text,
    match_limit int default 10,
    filter_project text default null,
    filter_type text default null,
    show_history boolean default false,
    include_unreviewed boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    requires_review boolean,
    rank real
)
language sql stable as $$
    select m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.updated_at, m.content_updated_at, m.last_accessed_at,
           m.requires_review,
           ts_rank(m.fts, websearch_to_tsquery('english', search_query)) as rank
    from memories m
    where m.fts @@ websearch_to_tsquery('english', search_query)
      -- Soft-deleted rows must never surface, even with show_history=true.
      -- Mirrors match_memories / match_memories_v2 — keeps the FTS leg
      -- consistent with the vector legs under rrf_merge.
      and m.deleted_at is null
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
      and (show_history
           or (m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
      -- Deriver review-gate (issue #552): see match_memories above.
      and (include_unreviewed or m.requires_review = false)
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by rank desc
    limit match_limit;
$$;

drop function if exists get_linked_memories(uuid[], text[], boolean);
drop function if exists get_linked_memories(uuid[], text[], boolean, boolean);
create or replace function get_linked_memories(
    memory_ids uuid[],
    link_types text[] default null,
    show_history boolean default false,
    include_unreviewed boolean default false
)
returns table(
    id uuid, name text, type text, project text,
    description text, content text, tags text[],
    updated_at timestamptz, content_updated_at timestamptz,
    last_accessed_at timestamptz,
    requires_review boolean,
    link_type text, link_strength float, linked_from uuid
)
language sql stable as $$
    select * from (
        select distinct on (sub.id)
            sub.id, sub.name, sub.type, sub.project, sub.description,
            sub.content, sub.tags,
            sub.updated_at, sub.content_updated_at, sub.last_accessed_at,
            sub.requires_review,
            sub.link_type, sub.link_strength, sub.linked_from
        from (
            -- Outgoing edges: source ∈ memory_ids, target resolved to head.
            select m.id, m.name, m.type, m.project, m.description, m.content,
                   m.tags, m.updated_at, m.content_updated_at, m.last_accessed_at,
                   m.requires_review,
                   l.link_type, l.strength as link_strength, l.source_id as linked_from
            from memory_links l
            join memories m on m.id = coalesce(
                case when show_history then l.target_id
                     else find_chain_head(l.target_id) end,
                l.target_id)
            where l.source_id = any(memory_ids)
              and not (m.id = any(memory_ids))
              and (link_types is null or l.link_type = any(link_types))
              -- Soft-deleted rows must never surface (mirrors match_memories).
              and m.deleted_at is null
              and (show_history
                   or (m.expired_at is null
                       and m.superseded_by is null
                       and (m.valid_to is null or m.valid_to > now())))
              -- Deriver review-gate (issue #552): see match_memories above.
              and (include_unreviewed or m.requires_review = false)
              and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
            union all
            -- Incoming edges: target ∈ memory_ids, source resolved to head.
            select m.id, m.name, m.type, m.project, m.description, m.content,
                   m.tags, m.updated_at, m.content_updated_at, m.last_accessed_at,
                   m.requires_review,
                   l.link_type, l.strength as link_strength, l.target_id as linked_from
            from memory_links l
            join memories m on m.id = coalesce(
                case when show_history then l.source_id
                     else find_chain_head(l.source_id) end,
                l.source_id)
            where l.target_id = any(memory_ids)
              and not (m.id = any(memory_ids))
              and (link_types is null or l.link_type = any(link_types))
              -- Soft-deleted rows must never surface (mirrors match_memories).
              and m.deleted_at is null
              and (show_history
                   or (m.expired_at is null
                       and m.superseded_by is null
                       and (m.valid_to is null or m.valid_to > now())))
              -- Deriver review-gate (issue #552): see match_memories above.
              and (include_unreviewed or m.requires_review = false)
              and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
        ) sub
        order by sub.id, sub.link_strength desc, sub.link_type, sub.linked_from
    ) deduped
    order by deduped.link_strength desc
    limit 10;
$$;


-- =========================================================================
-- Issue #740: Reshaped task_queue — drop approval columns, add
-- priority/assignee, new FSM.
--
-- FSM transitions (enforced in the interface; DB check guards the enum):
--   pending  -> claimed
--   claimed  -> running
--   running  -> done | failed | parked
--   done     -> (terminal)
--   failed   -> (terminal)
--   parked   -> (terminal)
--
-- Interface: agents/task_queue.py exposes enqueue(), claim_next()
-- (priority-ordered), and transition().
-- =========================================================================

create table if not exists task_queue (
  id uuid primary key default gen_random_uuid(),

  -- Intent
  goal text not null,
  scope_files text[] not null default '{}',

  -- Priority (higher = claimed first; FIFO ties) + optional worker assignee
  priority int not null default 0,
  assignee text,

  -- Lifecycle. `skipped_duplicate` (#931): dispatch-dedup terminal — issue
  -- already had a live PR or sibling row. Keep in lockstep with
  -- supabase/migrations/20260702120000_add_skipped_duplicate_status.sql.
  status text not null default 'pending'
    check (status in ('pending', 'claimed', 'running', 'done', 'failed', 'parked', 'skipped_duplicate')),
  claimed_at timestamptz,
  completed_at timestamptz,
  escalated_reason text,

  -- Dedup. sha256 hex (64 chars). Unique so a retrying worker cannot
  -- double-enqueue.
  idempotency_key text not null unique,

  -- Timestamps
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Dispatcher scan: highest-priority pending first, FIFO for ties.
create index if not exists idx_task_queue_pending_scan
  on task_queue(priority desc, created_at asc)
  where status = 'pending';

-- Dedicated updated_at trigger. The memories-shared update_updated_at()
-- references last_accessed_at/fts/project_key -- columns task_queue does
-- not have, so reusing it would error at runtime.
create or replace function update_task_queue_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists task_queue_updated_at on task_queue;
create trigger task_queue_updated_at
  before update on task_queue
  for each row execute function update_task_queue_updated_at();

-- NOTIFY trigger on INSERT — wake signal for cap-freed dispatch (#922).
-- When a task reaches pending (insert or after cap-freed transition), fire
-- NOTIFY on 'task_queue' channel so wake_driver wakes immediately instead of
-- waiting for the idle-timeout watchdog.
create or replace function notify_task_queue_insert()
returns trigger as $$
begin
  perform pg_notify(
    'task_queue',
    json_build_object(
      'id',     new.id,
      'goal',   new.goal,
      'status', new.status
    )::text
  );
  return new;
end;
$$ language plpgsql;

drop trigger if exists task_queue_notify on task_queue;
create trigger task_queue_notify
  after insert on task_queue
  for each row execute function notify_task_queue_insert();

-- RLS -- matches the Pillar 7 convention (allow-all under service/anon
-- key; app-layer gatekeeping is the interface functions). Hardening
-- to per-role policies is its own sweep.
alter table task_queue enable row level security;

create policy "Allow all for authenticated" on task_queue
  for all using (true) with check (true);

create policy "Allow all for anon" on task_queue
  for all to anon using (true) with check (true);


-- =========================================================================
-- #417 (Phase 3 recall ranking): drop broken IVFFLAT index on memories.embedding.
--
-- An IVFFLAT index existed on `memories.embedding` (lists=10) without ever
-- being declared in this file (created ad-hoc, pre-tracking). With ~390
-- live rows split across 10 lists and the default `ivfflat.probes = 1`,
-- queries returned at most ~39 candidates regardless of the SQL LIMIT —
-- silently capping recall and producing the q11/q15 misses in eval-recall.
-- A dedicated HNSW index (idx_memories_embedding_hnsw, defined elsewhere
-- with vector_cosine_ops) is sufficient at this corpus size; the planner
-- now falls back to seq-scan + top-N heapsort which gives exact recall in
-- ~80ms on 400 rows. Revisit when the corpus crosses ~5-10k rows and seq
-- scan becomes the bottleneck.
-- =========================================================================
drop index if exists public.memories_embedding_idx;


-- =========================================================================
-- #443 (Phase 5.3-β memory): FOK judgments table
--
-- Stores feeling-of-knowing verdicts for memory_recall events.
-- Tracks sufficiency of returned memories against queries, confidence
-- of the judgment, and optional linkage to task outcomes for calibration.
-- =========================================================================

create table if not exists fok_judgments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recall_event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  query           text NOT NULL,
  project         text,
  verdict         text NOT NULL CHECK (verdict IN ('sufficient','partial','insufficient','unknown','skipped')),
  confidence      real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  rationale       text,
  judge_model     text NOT NULL,
  judge_version   text NOT NULL,
  judged_at       timestamptz NOT NULL DEFAULT now(),
  action_taken    text CHECK (action_taken IN ('pass_through','gap_recorded','widened') OR action_taken IS NULL),
  action_at       timestamptz,
  outcome_id      uuid REFERENCES task_outcomes(id) ON DELETE SET NULL,
  outcome_correct boolean,
  UNIQUE (recall_event_id)
);

create index if not exists idx_fok_judgments_verdict ON fok_judgments(verdict, judged_at DESC);
create index if not exists idx_fok_judgments_query_project ON fok_judgments(project, query);
create index if not exists idx_fok_judgments_outcome ON fok_judgments(outcome_id) WHERE outcome_id IS NOT NULL;

ALTER TABLE fok_judgments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for authenticated" ON fok_judgments
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for anon" ON fok_judgments
  FOR ALL TO anon USING (true) WITH CHECK (true);


-- =========================================================================
-- C17 events substrate (Sprint #35 / #476)
-- Canonical events table for all observability writes. See
-- docs/design/c17-events-substrate.md for the design 1-pager.
-- Existing `events` table (above) stays during cutover wave (jarvis-v2-
-- redesign.md:1566 two-mode coexistence).
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS pg_cron;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'event_outcome') THEN
    CREATE TYPE event_outcome AS ENUM ('success', 'failure', 'timeout', 'partial');
  END IF;
END$$;

CREATE TABLE IF NOT EXISTS events_canonical (
  event_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id        uuid NOT NULL,
  parent_event_id uuid NULL,
  ts              timestamptz NOT NULL DEFAULT now(),
  actor           text NOT NULL,
  action          text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  outcome         event_outcome NULL,
  cost_tokens     int NULL,
  cost_usd        numeric(12, 6) NULL,
  redacted        bool NOT NULL DEFAULT false,
  degraded        bool NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_events_canonical_trace_ts
  ON events_canonical (trace_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_canonical_actor_ts
  ON events_canonical (actor, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_canonical_action_ts
  ON events_canonical (action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_canonical_cost
  ON events_canonical (ts DESC)
  WHERE cost_usd IS NOT NULL;

CREATE OR REPLACE FUNCTION notify_events_canonical()
RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'events_canonical',
    json_build_object(
      'event_id', NEW.event_id,
      'trace_id', NEW.trace_id,
      'action',   NEW.action,
      'actor',    NEW.actor
    )::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_canonical_notify ON events_canonical;
CREATE TRIGGER events_canonical_notify
  AFTER INSERT ON events_canonical
  FOR EACH ROW EXECUTE FUNCTION notify_events_canonical();

ALTER TABLE events_canonical ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for authenticated" ON events_canonical
  FOR ALL USING (true) WITH CHECK (true);
-- Anon access — INSERT/UPDATE/DELETE gated on `actor` (provenance field).
-- Slices 3 + 3.5, #542 + #565.
CREATE POLICY "Anon select" ON events_canonical
  FOR SELECT TO anon USING (true);
CREATE POLICY "Anon sandcastle update" ON events_canonical
  FOR UPDATE TO anon
  USING (actor LIKE 'sandcastle:%')
  WITH CHECK (actor LIKE 'sandcastle:%');
CREATE POLICY "Anon sandcastle delete" ON events_canonical
  FOR DELETE TO anon
  USING (actor LIKE 'sandcastle:%');
CREATE POLICY "Anon sandcastle insert" ON events_canonical
  FOR INSERT TO anon
  WITH CHECK (actor LIKE 'sandcastle:%');

CREATE MATERIALIZED VIEW IF NOT EXISTS events_cost_by_day_mv AS
SELECT
  date_trunc('day', ts)                       AS day,
  actor,
  payload->>'gen_ai.request.model'            AS model,
  SUM(cost_tokens)                            AS total_tokens,
  SUM(cost_usd)                               AS total_usd,
  COUNT(*)                                    AS n_events
FROM events_canonical
WHERE cost_usd IS NOT NULL
  AND degraded = false
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_cost_by_day_mv_uniq
  ON events_cost_by_day_mv (day, actor, model);

CREATE MATERIALIZED VIEW IF NOT EXISTS events_last_run_by_actor_mv AS
SELECT
  actor,
  action,
  MAX(ts) FILTER (WHERE outcome = 'success') AS last_success_at,
  MAX(ts)                                    AS last_event_at,
  COUNT(*)                                   AS n_events
FROM events_canonical
GROUP BY actor, action
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_last_run_by_actor_mv_uniq
  ON events_last_run_by_actor_mv (actor, action);

-- pg_cron schedules registered in the migration file
-- (cron.schedule(...) is idempotent on (jobname); not duplicated here to
--  keep schema.sql declarative — the cron jobs live in cron.job).


-- =========================================================================
-- Milestone #42 — /learn skill — always-gate review surface (issue #681)
--
-- Adds requires_review and merge_targets columns to the memories table so
-- deriver/dreamer candidates land in a "pending review" state. Also ships
-- memory_review_decide (single dispatcher for all review actions) and
-- memory_review_list (fetch pending rows in queue order).
--
-- See supabase/migrations/20260518000001_add_memory_review_columns.sql
-- for the deployable migration. This section keeps schema.sql in sync.
-- =========================================================================

ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS requires_review BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_memories_requires_review
  ON memories(id) WHERE requires_review;

ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS merge_targets UUID[];

CREATE INDEX IF NOT EXISTS idx_memories_merge_targets
  ON memories(id) WHERE merge_targets IS NOT NULL;

ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS reject_reason TEXT;

CREATE OR REPLACE FUNCTION memory_review_decide(
    action TEXT,
    candidate_id UUID,
    edited_name TEXT DEFAULT NULL,
    edited_description TEXT DEFAULT NULL,
    edited_content TEXT DEFAULT NULL,
    edited_tags TEXT[] DEFAULT NULL,
    reject_reason TEXT DEFAULT NULL,
    reviewer TEXT DEFAULT 'owner'
)
RETURNS JSONB
LANGUAGE plpgsql VOLATILE
AS $$
DECLARE
    v_candidate RECORD;
    v_result JSONB;
    v_new_id UUID;
    v_superseded_count INT;
BEGIN
    SELECT * INTO v_candidate FROM memories WHERE id = candidate_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'error', 'error', 'candidate_not_found');
    END IF;

    IF NOT v_candidate.requires_review THEN
        RETURN jsonb_build_object('status', 'already_reviewed', 'candidate_id', candidate_id);
    END IF;

    IF action NOT IN ('accept', 'accept_with_edit', 'reject', 'merge', 'approve', 'reject_classifier') THEN
        RAISE EXCEPTION 'Unknown action: %. Valid actions: accept, accept_with_edit, reject, merge, approve, reject_classifier', action;
    END IF;

    CASE action
        WHEN 'accept' THEN
            UPDATE memories SET requires_review = false WHERE id = candidate_id;
            v_result := jsonb_build_object('status', 'ok', 'action', 'accept', 'candidate_id', candidate_id);

        WHEN 'accept_with_edit' THEN
            IF edited_content IS NULL AND edited_name IS NULL AND edited_description IS NULL AND edited_tags IS NULL THEN
                RAISE EXCEPTION 'accept_with_edit requires at least one edited_* field';
            END IF;
            UPDATE memories
            SET requires_review = false,
                name = COALESCE(edited_name, name),
                description = COALESCE(edited_description, description),
                content = COALESCE(edited_content, content),
                tags = COALESCE(edited_tags, tags)
            WHERE id = candidate_id;
            v_result := jsonb_build_object('status', 'ok', 'action', 'accept_with_edit', 'candidate_id', candidate_id);

        WHEN 'reject' THEN
            UPDATE memories
            SET requires_review = false,
                reject_reason = COALESCE(memory_review_decide.reject_reason, 'no reason given')
            WHERE id = candidate_id;
            v_result := jsonb_build_object('status', 'ok', 'action', 'reject', 'candidate_id', candidate_id,
                'reject_reason', COALESCE(memory_review_decide.reject_reason, 'no reason given'));

        WHEN 'merge' THEN
            IF v_candidate.merge_targets IS NULL OR array_length(v_candidate.merge_targets, 1) IS NULL THEN
                RAISE EXCEPTION 'merge action requires candidate with non-empty merge_targets';
            END IF;
            INSERT INTO memories (project, name, type, description, content, tags, source_provenance, requires_review)
            VALUES (v_candidate.project, v_candidate.name, v_candidate.type,
                    v_candidate.description, v_candidate.content, v_candidate.tags,
                    v_candidate.source_provenance, false)
            RETURNING id INTO v_new_id;
            UPDATE memories
            SET superseded_by = v_new_id, valid_to = now(), expired_at = now()
            WHERE id = ANY(v_candidate.merge_targets) AND superseded_by IS NULL;
            GET DIAGNOSTICS v_superseded_count = ROW_COUNT;
            INSERT INTO memory_links (source_id, target_id, link_type, strength)
            SELECT v_new_id, unnest_id, 'supersedes', 1.0
            FROM unnest(v_candidate.merge_targets) AS unnest_id
            ON CONFLICT (source_id, target_id, link_type) DO NOTHING;
            UPDATE memories SET requires_review = false WHERE id = candidate_id;
            v_result := jsonb_build_object('status', 'ok', 'action', 'merge', 'candidate_id', candidate_id,
                'canonical_id', v_new_id, 'superseded_count', v_superseded_count);

        WHEN 'approve' THEN
            UPDATE memories SET requires_review = false WHERE id = candidate_id;
            v_result := jsonb_build_object('status', 'ok', 'action', 'approve', 'candidate_id', candidate_id);

        WHEN 'reject_classifier' THEN
            UPDATE memories
            SET requires_review = false,
                reject_reason = COALESCE(memory_review_decide.reject_reason, 'rejected by classifier review')
            WHERE id = candidate_id;
            v_result := jsonb_build_object('status', 'ok', 'action', 'reject_classifier', 'candidate_id', candidate_id,
                'reject_reason', COALESCE(memory_review_decide.reject_reason, 'rejected by classifier review'));
    END CASE;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION memory_review_list(
    queue TEXT DEFAULT 'candidate',
    project_filter TEXT DEFAULT NULL,
    limit_count INT DEFAULT 20
)
RETURNS TABLE(
    id UUID, name TEXT, type TEXT, project TEXT,
    description TEXT, content TEXT, tags TEXT[],
    source_provenance TEXT, requires_review BOOLEAN,
    merge_targets UUID[], reject_reason TEXT, created_at TIMESTAMPTZ
)
LANGUAGE sql STABLE
AS $$
    SELECT m.id, m.name, m.type, m.project,
           m.description, m.content, m.tags,
           m.source_provenance, m.requires_review,
           m.merge_targets, m.reject_reason, m.created_at
    FROM memories m
    WHERE m.requires_review
      AND m.deleted_at IS NULL
      AND (project_filter IS NULL OR m.project = project_filter OR (m.project IS NULL AND project_filter = ''))
      AND (CASE queue
        WHEN 'candidate' THEN m.source_provenance NOT LIKE 'classifier:%'
        WHEN 'classifier' THEN m.source_provenance LIKE 'classifier:%'
        ELSE TRUE
      END)
    ORDER BY
      CASE WHEN m.merge_targets IS NOT NULL THEN 0 ELSE 1 END,
      m.created_at ASC
    LIMIT limit_count;
$$;

-- =====================================================================
-- comm_patterns — communication-pattern instances (#580, ADR 0004)
-- One row per detected pattern instance. Stop-hook extractor classifies
-- user→assistant turns and writes here. Cross-device aggregate; no
-- project pinning (global scope per ADR 0004 §3).
-- =====================================================================

create table if not exists comm_patterns (
  id uuid default gen_random_uuid() primary key,

  -- Origin
  device text not null,
  session_id text not null,
  message_idx int not null,
  captured_at timestamptz not null,

  -- Classifier output
  primary_label text not null check (primary_label in (
    'correction_wrong_direction',
    'correction_incomplete',
    'affirmation',
    'affirmation_with_redirect',
    'preference_directive',
    'meta_protocol'
  )),
  subtype text,
  confidence numeric(3,2) not null check (confidence >= 0 and confidence <= 1),

  -- Anchor (raw text + redaction marker; ADR 0004 §2)
  anchor_quote text not null,
  redacted boolean not null default false,

  -- Day-1 embeddings (Voyage 512-dim, matches `memories.embedding`)
  embedding vector(512),

  -- Provenance + bookkeeping
  source_provenance text not null,
  created_at timestamptz default now()
);

create index if not exists idx_comm_patterns_label_captured
  on comm_patterns (primary_label, captured_at desc);

create unique index if not exists idx_comm_patterns_dedup
  on comm_patterns (device, session_id, message_idx);

create index if not exists idx_comm_patterns_no_embedding
  on comm_patterns (id) where embedding is null;

alter table comm_patterns enable row level security;

create policy "Allow all for authenticated" on comm_patterns
  for all using (true) with check (true);

-- Per-(device, session) Stop-hook watermark for idempotent extraction.
-- Extractor reads the watermark, only classifies messages with idx >
-- last_message_idx, then bumps the watermark in the same transaction.
create table if not exists comm_patterns_watermark (
  device text not null,
  session_id text not null,
  last_message_idx int not null default -1,
  updated_at timestamptz default now(),
  primary key (device, session_id)
);

alter table comm_patterns_watermark enable row level security;

create policy "Allow all for authenticated" on comm_patterns_watermark
  for all using (true) with check (true);


-- =========================================================================
-- Implicit memory derivation: Deriver/Dreamer columns (issue #552, Slice 1)
--
-- Adds the storage columns the derivation subsystem depends on. Existing
-- columns relied on by the subsystem:
--   - confidence real default 0.5   (added Phase 1, line 592)
--   - superseded_by uuid            (added Phase 1, line 585)
--   - source_provenance text        (added Phase 1, line 599)
--
-- Pre-migration collision check (scripts/check-memory-deriver-schema.py)
-- must run before applying this migration, asserting superseded_by has
-- compatible semantics. See AC: "superseded_by either does not exist OR
-- has compatible semantics; aborts otherwise".
-- =========================================================================

-- Provenance namespaces for rows added via this subsystem:
--   * source_provenance = 'hook:deriver'  — synchronous Deriver hook writes
--   * source_provenance = 'task:dreamer'  — async Dreamer batch writes
-- Both land with requires_review=true; owner accept via memory_review_decide
-- is the only path to live memory in v1.

-- requires_review: always-gate review flag. Every Deriver/Dreamer write
-- lands requires_review=true regardless of confidence.  Recall hides these
-- by default; opt-in via include_unreviewed=true.  Owner accept via
-- memory_review_decide is the only path to live memory in v1.
-- Decision: 31ebba19-adb6-4ad0-ac33-ceac5bc5cea2 (ADR-0003 §Q4).
-- Default covers existing rows at zero I/O cost (PG 11+ catalog-only ADD COLUMN).
alter table memories add column if not exists requires_review boolean
    not null default false;

-- derivation_run_id: provenance pointer linking the memory back to the
-- specific Deriver/Dreamer run that created it.  Used for auditing and
-- for the Dreamer's "re-derive if superseded" skip logic.  NULL for
-- pre-derivation rows (catalog-only metadata; no backfill needed).
alter table memories add column if not exists derivation_run_id uuid;

-- merge_targets: non-NULL + non-empty signals a merge proposal.  Recall
-- must skip rows where merge_targets is non-null AND non-empty (showing
-- them only via a dedicated review endpoint).  CHECK forbids the empty
-- array so the boundary is binary (NULL = not a proposal, non-NULL = is
-- a proposal) and the GIN partial predicate stays aligned with the recall
-- filter.  Decision d162cca4-25ba-4342-b6e2-c1c92bd2ba78 (ADR-0003 §Q3).
alter table memories add column if not exists merge_targets uuid[];

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'memories_merge_targets_non_empty'
          and conrelid = 'memories'::regclass
    ) then
        alter table memories add constraint memories_merge_targets_non_empty
            check (merge_targets is null or array_length(merge_targets, 1) > 0);
    end if;
end $$;

-- Index: Deriver/Dreamer scan for rows needing review (SessionStart hook
-- surfaces pending count).  Also covers the review-decision flow where
-- the owner fetches candidates.
create index if not exists idx_memories_requires_review
    on memories(requires_review)
    where requires_review = true;

-- Index: recall filter — rows with non-empty merge_targets are proposals,
-- skipped by default recall.  GIN index for array containment checks.
-- Partial predicate aligned with the CHECK constraint above (NULL is the
-- only "not a proposal" state; non-NULL implies non-empty).
create index if not exists idx_memories_merge_targets
    on memories using gin (merge_targets)
    where merge_targets is not null;

-- Index: derivation_run_id equality lookups for the Dreamer's "re-derive
-- if superseded" skip logic.  Partial predicate keeps the index small —
-- pre-derivation rows have NULL run_id and are not queried here.
create index if not exists idx_memories_derivation_run_id
    on memories(derivation_run_id)
    where derivation_run_id is not null;


-- =========================================================================
-- Global Task Sources — Issue #679: AFK loop input registry
-- Non-repo, recurring tasks. Advancer ticks due rows into events queue.
-- =========================================================================

create table if not exists global_task_sources (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  body text,
  dispatcher_skill text not null
    check (dispatcher_skill in ('research', 'self-improve', 'status-record', 'last-work-report')),
  output_sink text not null
    -- 'event_reemit' is RESERVED / NOT-YET-IMPLEMENTED: the enum value exists so
    -- the dispatcher_sink_compatibility matrix below can already forbid the
    -- nonsensical pairings, but no advancer/orchestrator path consumes it yet
    -- (tracked under #679). Treat a row carrying it as inert until wired.
    check (output_sink in ('memory', 'telegram_digest', 'event_reemit')),
  payload jsonb default '{}',
  -- cadence floor of 1 second: the advancer divides by EXTRACT(EPOCH FROM
  -- cadence) when counting lapsed intervals, so a zero cadence is a
  -- divide-by-zero that aborts the whole advance transaction, and a sub-second
  -- cadence makes the int(epoch) dedup_key collide across ticks. NULL stays a
  -- valid one-shot.
  cadence interval check (cadence is null or cadence >= interval '1 second'),
  last_run timestamptz,
  next_run timestamptz,
  enabled bool not null default true,
  on_lapse text not null default 'coalesce'
    check (on_lapse in ('coalesce', 'fire_per_interval')),
  created_at timestamptz not null default now()
);

-- Dispatcher/sink compatibility matrix (enforced at DB level).
-- status-record -> memory only (it only ever writes a status memory row);
-- research -> any sink; self-improve / last-work-report -> any sink EXCEPT
-- event_reemit (re-emitting an event back into the queue from a self-improve
-- or report run would loop the advancer against its own output — neither skill
-- produces an event-shaped result, so the pairing is incoherent, not just
-- unimplemented).
-- drop-then-add (not the add-only DO/EXCEPTION guard the sibling constraints use):
-- this matrix was TIGHTENED to exclude event_reemit for self-improve /
-- last-work-report, so a DB carrying the earlier, looser constraint must have it
-- replaced. An add-only guard would hit duplicate_object and silently keep the
-- old, permissive form. DROP ... IF EXISTS is a no-op on a fresh DB.
alter table global_task_sources drop constraint if exists dispatcher_sink_compatibility;
alter table global_task_sources add constraint dispatcher_sink_compatibility
  check (
    (dispatcher_skill = 'status-record' and output_sink = 'memory') or
    (dispatcher_skill = 'research') or
    (dispatcher_skill = 'self-improve' and output_sink <> 'event_reemit') or
    (dispatcher_skill = 'last-work-report' and output_sink <> 'event_reemit')
  );

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

-- ===========================================================================
-- credential_registry (Pillar 9): metadata inventory of credentials — service,
-- env-var NAME, storage location, rotation/expiry. NEVER secret values (see
-- mcp-memory/handlers/credential.py). Applied to remote as migrations
-- 20260415082814 (create) + 20260708044124 (RLS); documented here per #326.
-- ===========================================================================
create table if not exists credential_registry (
  id uuid primary key default gen_random_uuid(),
  service text not null,
  env_var text not null unique,
  stored_in text not null default '.env',
  scope text not null default 'jarvis',
  created_at timestamptz default now(),
  expires_at timestamptz,
  last_rotated_at timestamptz,
  rotation_notes text,
  notes text,
  -- Defence-in-depth: reject rows whose metadata columns look like a raw secret.
  check (
    env_var !~ '^(eyJ|sk-|ghp_|ghs_|AKIA|xox[bpras]-)'
    and (rotation_notes is null or rotation_notes !~ '(eyJ|sk-|ghp_|ghs_|AKIA)')
    and (notes is null or notes !~ '(eyJ|sk-|ghp_|ghs_|AKIA)')
  )
);

-- RLS: allow-all convention (service_role bypasses). Enabled to clear the
-- Supabase rls_disabled_in_public ERROR; access is unchanged (app-layer trust).
alter table credential_registry enable row level security;
drop policy if exists "Allow all for authenticated" on credential_registry;
drop policy if exists "Allow all for anon" on credential_registry;
create policy "Allow all for authenticated" on credential_registry
  for all using (true) with check (true);
create policy "Allow all for anon" on credential_registry
  for all to anon using (true) with check (true);

-- ===========================================================================
-- audit_log: fire-and-forget trail of MCP tool invocations. Applied to remote
-- as migrations 20260415113317 (create) + 20260708044124 (RLS); documented
-- here per #326.
-- ===========================================================================
create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  "timestamp" timestamptz default now(),
  agent_id text,
  tool_name text not null,
  action text not null,
  target text,
  details jsonb default '{}'::jsonb,
  outcome text default 'success'
);

create index if not exists idx_audit_log_timestamp on audit_log ("timestamp" desc);
create index if not exists idx_audit_log_tool_name on audit_log (tool_name);

alter table audit_log enable row level security;
drop policy if exists "Allow all for authenticated" on audit_log;
drop policy if exists "Allow all for anon" on audit_log;
create policy "Allow all for authenticated" on audit_log
  for all using (true) with check (true);
create policy "Allow all for anon" on audit_log
  for all to anon using (true) with check (true);

-- ===========================================================================
-- review_debt: sub-MAJOR code-review findings, collected + clustered (#1211).
-- Applied to remote as migration 20260721120000_create_review_debt.sql;
-- documented here per #326 (schema.sql is aspirational; the migration executes).
-- The review-debt collector persists MEDIUM/INFO findings that never block a
-- merge, dedups by (module_area + rule + file), clusters by module_area, and
-- auto-files one review-debt-cluster issue at threshold.
-- ===========================================================================
create table if not exists review_debt (
  id            uuid primary key default gen_random_uuid(),
  dedup_key     text not null unique,   -- module_area + rule + file (no desc/line)
  module_area   text not null,          -- parent dir; the clustering bucket
  severity      text not null,          -- MEDIUM | INFO
  weight        numeric not null default 0.5,   -- per-severity contribution
  rule          text not null default '',
  file          text not null default '',
  seen_count    integer not null default 1,     -- raw occurrences (incremented)
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  issued_state  text not null default 'open_debt',  -- open_debt | clustered
  cluster_issue integer,                -- issue number once clustered
  source_pr     text
);

create index if not exists idx_review_debt_module_area on review_debt (module_area);
create index if not exists idx_review_debt_issued_state on review_debt (issued_state);
create index if not exists idx_review_debt_last_seen on review_debt (last_seen_at desc);

-- Upsert RPC increments seen_count on a repeat finding (merge-duplicates can't).
create or replace function review_debt_upsert(
  p_dedup_key text, p_module_area text, p_severity text, p_weight numeric,
  p_rule text, p_file text, p_source_pr text, p_seen_at timestamptz default now()
) returns review_debt language plpgsql security invoker set search_path = public as $$
declare result review_debt;
begin
  insert into review_debt as rd (
    dedup_key, module_area, severity, weight, rule, file,
    source_pr, first_seen_at, last_seen_at)
  values (p_dedup_key, p_module_area, p_severity, p_weight, p_rule, p_file,
    p_source_pr, p_seen_at, p_seen_at)
  on conflict (dedup_key) do update
    set seen_count = rd.seen_count + 1, last_seen_at = excluded.last_seen_at,
        source_pr = excluded.source_pr,
        issued_state = case when rd.issued_state = 'clustered' then 'clustered'
                            else 'open_debt' end
  returning rd.* into result;
  return result;
end; $$;

alter table review_debt enable row level security;
drop policy if exists "Allow all for authenticated" on review_debt;
drop policy if exists "Allow all for anon" on review_debt;
create policy "Allow all for authenticated" on review_debt
  for all using (true) with check (true);
create policy "Allow all for anon" on review_debt
  for all to anon using (true) with check (true);
