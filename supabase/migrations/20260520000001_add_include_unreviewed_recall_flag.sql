-- Add `include_unreviewed` parameter to recall-path RPCs (issue #685, Slice 5)
--
-- Default recall now excludes `requires_review=true` rows (the always-gate,
-- #552). This slice adds an opt-in flag so the eval harness / Deriver /
-- Dreamer can surface pending-candidate rows when needed. merge_targets
-- rows (merge proposals) are ALWAYS filtered, regardless of the flag.
--
-- Round 4 additions (2026-05-26):
--   * `requires_review` boolean added to every RPC's `returns table(...)`
--     and SELECT projection. Without this, the Python-side CONFIDENCE_FLOOR
--     entrenchment floor for unreviewed rows (recall.py:apply_temporal_scoring)
--     was dead code — PostgREST never materialised the column, so
--     `row.get("requires_review")` always returned None.
--   * `keyword_search_memories` + `get_linked_memories` now apply
--     `m.deleted_at is null` UNCONDITIONALLY (outside show_history), matching
--     the round-3 fix to `match_memories` / `match_memories_v2`. Without
--     this, show_history=true surfaced soft-deleted rows from the FTS / link
--     legs while the vector leg hid them, producing non-deterministic RRF
--     fusion.
--
-- Decision reference: 8f846597-2da0-44e0-af0c-0e65b3f36cbb (recall always-gate).
-- See also: supabase/migrations/20260518000001_add_memory_review_columns.sql.

-- =============================================================================
-- match_memories_v2 (primary embedding slot)
-- =============================================================================
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
      and (include_unreviewed or m.requires_review = false)
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by m.embedding_v2 <=> query_embedding
    limit match_limit;
$$;

-- =============================================================================
-- match_memories (fallback embedding slot)
-- =============================================================================
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
      -- Align with match_memories_v2: soft-deleted rows are always hidden,
      -- regardless of show_history. Pre-existing asymmetry corrected here so
      -- the two embedding slots return the same row set under
      -- show_history=true + include_unreviewed=true (RRF determinism).
      and m.deleted_at is null
      and 1 - (m.embedding <=> query_embedding) >= similarity_threshold
      and (filter_project is null or m.project = filter_project or m.project is null)
      and (filter_type is null or m.type = filter_type)
      and (show_history
           or (m.expired_at is null
               and m.superseded_by is null
               and (m.valid_to is null or m.valid_to > now())))
      and (include_unreviewed or m.requires_review = false)
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by m.embedding <=> query_embedding
    limit match_limit;
$$;

-- =============================================================================
-- keyword_search_memories (FTS leg)
-- =============================================================================
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
      and (include_unreviewed or m.requires_review = false)
      and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
    order by rank desc
    limit match_limit;
$$;

-- =============================================================================
-- get_linked_memories (1-hop BFS for link expansion)
-- =============================================================================
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
            -- Outgoing edges: source in memory_ids, target resolved to head.
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
              and (include_unreviewed or m.requires_review = false)
              and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
            union all
            -- Incoming edges: target in memory_ids, source resolved to head.
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
              and (include_unreviewed or m.requires_review = false)
              and (m.merge_targets is null or array_length(m.merge_targets, 1) = 0)
        ) sub
        order by sub.id, sub.link_strength desc, sub.link_type, sub.linked_from
    ) deduped
    order by deduped.link_strength desc
    limit 10;
$$;
