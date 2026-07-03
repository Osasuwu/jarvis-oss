-- Memory deriver Slice 1 — schema migration (#552, PR #631)
--
-- Mirrors the deriver block at the bottom of mcp-memory/schema.sql so the
-- Supabase CLI applies the columns/constraint/indexes to the live shared
-- database. Pre-migration collision precheck: scripts/check-memory-deriver-schema.py.
--
-- Atomicity: the Supabase CLI wraps each migration file in a single
-- transaction. If any statement fails, the whole file rolls back and the
-- DB stays at the prior state. For manual psql replay, wrap the body in
-- BEGIN; ... COMMIT; explicitly.
--
-- Index trade-off: CREATE INDEX … without CONCURRENTLY takes a SHARE lock
-- on memories for the duration of the build. The shared `memories` table
-- is small enough at this stage that this is acceptable; switching to
-- CONCURRENTLY would require disabling the per-file transaction wrapper.
-- Revisit if/when the table grows past the point where SHARE-locked GIN
-- builds block writes for >1s.
--
-- Provenance namespaces for rows added via this subsystem:
--   * source_provenance = 'hook:deriver' — synchronous Deriver hook writes
--   * source_provenance = 'task:dreamer' — async Dreamer batch writes
-- Both land with requires_review=true; owner accept via memory_review_decide
-- is the only path to live memory in v1.

-- requires_review: always-gate review flag (ADR-0003 §Q4,
-- decision 31ebba19-adb6-4ad0-ac33-ceac5bc5cea2).
-- Default covers existing rows at zero I/O cost (PG 11+ catalog-only ADD COLUMN).
alter table memories add column if not exists requires_review boolean
    not null default false;

-- derivation_run_id: pointer to the Deriver/Dreamer run that wrote the row.
alter table memories add column if not exists derivation_run_id uuid;

-- merge_targets: non-NULL + non-empty = merge proposal (ADR-0003 §Q3,
-- decision d162cca4-25ba-4342-b6e2-c1c92bd2ba78).
alter table memories add column if not exists merge_targets uuid[];

-- CHECK forbids empty arrays so NULL is the only "not a proposal" state.
-- Keeps the GIN partial predicate aligned with the recall filter.
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

-- Index: SessionStart scan for rows pending owner review.
create index if not exists idx_memories_requires_review
    on memories(requires_review)
    where requires_review = true;

-- Index: recall filter skips merge-proposal rows.
create index if not exists idx_memories_merge_targets
    on memories using gin (merge_targets)
    where merge_targets is not null;

-- Index: Dreamer "re-derive if superseded" equality lookups.
create index if not exists idx_memories_derivation_run_id
    on memories(derivation_run_id)
    where derivation_run_id is not null;

-- =========================================================================
-- Recall RPC redefinitions: review-gate enforcement (C4 fix, PR #631)
--
-- The previous match_memories / keyword_search_memories / get_linked_memories
-- / match_memories_v2 RPCs had no `requires_review = false` or
-- `merge_targets is null` filter. The moment any Deriver/Dreamer write
-- lands with `requires_review=true` or populates `merge_targets`, those
-- rows surface in every recall path including redrobot's shared instance.
-- Filters are unconditional (independent of show_history) — review-pending
-- rows are not "history", they are unverified writes.
--
-- Note on the `array_length(m.merge_targets, 1) = 0` branch below: the
-- CHECK constraint `memories_merge_targets_non_empty` already forbids empty
-- arrays, and `array_length('{}'::uuid[], 1)` returns NULL in PostgreSQL
-- (not 0) anyway — so the `= 0` predicate is technically unreachable.
-- Kept as a belt-and-braces guard against future constraint relaxation;
-- the runtime cost is negligible and `merge_targets is null` short-circuits
-- via the partial GIN index.
-- =========================================================================

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
           or (m.deleted_at is null
               and m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
      and m.requires_review = false
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by m.embedding <=> query_embedding
    limit match_limit;
$$;

drop function if exists match_memories_v2(vector, int, float, text, text, boolean);
create or replace function match_memories_v2(
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
      and m.requires_review = false
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by m.embedding_v2 <=> query_embedding
    limit match_limit;
$$;

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
           or (m.deleted_at is null
               and m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
      and m.requires_review = false
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by rank desc
    limit match_limit;
$$;

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
        select distinct on (sub.id)
            sub.id, sub.name, sub.type, sub.project, sub.description,
            sub.content, sub.tags,
            sub.updated_at, sub.content_updated_at, sub.last_accessed_at,
            sub.link_type, sub.link_strength, sub.linked_from
        from (
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
                   or (m.deleted_at is null
                       and m.expired_at is null
                       and m.superseded_by is null
                       and (m.valid_to is null or m.valid_to > now())))
              and m.requires_review = false
              and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
            union all
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
                   or (m.deleted_at is null
                       and m.expired_at is null
                       and m.superseded_by is null
                       and (m.valid_to is null or m.valid_to > now())))
              and m.requires_review = false
              and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
        ) sub
        order by sub.id, sub.link_strength desc, sub.link_type, sub.linked_from
    ) deduped
    order by deduped.link_strength desc
    limit 10;
$$;
