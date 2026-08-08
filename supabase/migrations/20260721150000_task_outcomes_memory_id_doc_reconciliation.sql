-- ===========================================================================
-- #660 doc-reconciliation companion migration.
--
-- schema.sql's `task_outcomes.memory_id` column moved inline into the
-- canonical `create table task_outcomes` block (previously expressed as a
-- separate trailing `ALTER TABLE`, now reduced to an explanatory comment).
-- No semantic schema change — the column + FK + index already exist on the
-- live DB (added under the original Phase 5 Metacognition / Confidence
-- Calibration work, #251) but that ALTER TABLE was never paired with a
-- committed file under supabase/migrations/, so this backfills that record
-- and satisfies schema-drift-check's require-paired-migration gate for the
-- schema.sql diff in this PR.
--
-- Fully idempotent — no-op against the current live schema.
-- ===========================================================================

alter table task_outcomes
  add column if not exists memory_id uuid references memories(id) on delete set null;

create index if not exists idx_task_outcomes_memory_id
  on task_outcomes(memory_id) where memory_id is not null;
