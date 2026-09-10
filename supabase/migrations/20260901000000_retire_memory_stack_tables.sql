-- Retire the memory-stack tables/views/functions (#1801).
--
-- mcp-memory/ (and its Supabase-backed recall/consolidation/deriver/FOK
-- subsystems) is being demolished. This migration performs the live
-- decommission of everything that referenced the six EXCLUDED tables
-- (memories, memory_links, memory_review_queue, episodes, known_unknowns,
-- fok_judgments) so the database matches the new supabase/schema.sql, which
-- no longer declares any of them.
--
-- Order: functions/views that reference the excluded tables first (drop
-- dynamically by name via pg_proc — several were redefined multiple times
-- across migrations with different signatures, and CASCADE on the DROP
-- FUNCTION handles any dependent view/policy automatically), then the
-- memory_calibration view, then the one cross-boundary FK from a KEPT table
-- (task_outcomes.memory_id), then the excluded tables themselves.

-- =========================================================================
-- 1. Drop every function that references an excluded table, regardless of
--    how many times it was redefined/overloaded across prior migrations.
-- =========================================================================
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure as sig
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = any(array[
        'memory_calibration_summary',
        'find_consolidation_clusters',
        'archive_memories',
        'match_memories',
        'match_memories_v2',
        'keyword_search_memories',
        'get_linked_memories',
        'find_similar_memories',
        'touch_memories',
        'find_chain_head',
        'apply_consolidation_plan',
        'rollback_consolidation',
        'approve_consolidation',
        'reject_consolidation',
        'apply_evolution_plan',
        'rollback_evolution',
        'fok_calibration_summary',
        'memory_review_decide',
        'memory_review_list',
        'merge_section_into_memory_upsert'
      ])
  loop
    execute format('drop function if exists %s cascade', r.sig);
  end loop;
end $$;

-- =========================================================================
-- 2. Drop the memory_calibration view (joins memories + task_outcomes).
-- =========================================================================
drop view if exists memory_calibration cascade;

-- =========================================================================
-- 3. Drop the sole cross-boundary FK from a KEPT table into an EXCLUDED one.
--    Drops the dependent partial index (idx_task_outcomes_memory_id)
--    automatically.
-- =========================================================================
alter table task_outcomes drop column if exists memory_id;

-- =========================================================================
-- 4. Drop the six excluded tables. CASCADE covers any remaining
--    intra-excluded-table FK not individually enumerated above.
-- =========================================================================
drop table if exists
  memory_links,
  memory_review_queue,
  known_unknowns,
  fok_judgments,
  episodes,
  memories
  cascade;
