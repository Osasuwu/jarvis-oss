-- #1491 (#953 fix): PostgREST upsert generates a bare ON CONFLICT (dedup_key),
-- which Postgres cannot infer from the partial unique index
-- idx_events_dedup_key (WHERE dedup_key IS NOT NULL) — every task_failed /
-- task_done emission from wake_driver died with 42P10 and was swallowed by
-- design ("emit must not block transition"), so re-drive/escalation never ran.
-- A full UNIQUE constraint (default NULLS DISTINCT) preserves prior semantics
-- exactly — multiple NULLs stay allowed, non-null keys stay unique — and makes
-- the bare conflict target inferable. Constraint added before the drop so
-- enforcement has no gap. Applied to prod 2026-08-10 (0 rows carried dedup_key,
-- so no conflict risk existed).
alter table events add constraint events_dedup_key_key unique (dedup_key);
drop index if exists idx_events_dedup_key;
