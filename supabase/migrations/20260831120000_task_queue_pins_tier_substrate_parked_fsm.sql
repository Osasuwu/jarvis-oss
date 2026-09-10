-- #1119 (S3, milestone 58): structured pin columns, tier/substrate columns,
-- and the parked-is-non-terminal FSM change.
--
-- FSM after this migration:
--   pending  -> claimed
--   claimed  -> running | parked
--   running  -> done | failed | parked | skipped_duplicate
--   parked   -> pending   (unpark; resumes through the normal drain)
--   done, failed, skipped_duplicate -> (terminal)
--
-- Structured pins replace the issue_number-only target with a general
-- (target_repo, target_type, target_number, target_branch) tuple so a row
-- can address any spawn target, not just a jarvis issue. issue_number is
-- kept as a deprecated mirror (drop = follow-up slice) — backfilled here,
-- not removed.
--
-- Historical parked rows predate the non-terminal semantics and were parked
-- for reasons the new FSM does not model the same way; they are converted to
-- failed BEFORE the index rebuild below, per decision f24ad617.
-- Keep in lockstep with mcp-memory/schema.sql.

alter table task_queue add column if not exists target_repo text;
alter table task_queue add column if not exists target_type text;
alter table task_queue add column if not exists target_number int;
alter table task_queue add column if not exists target_branch text;
alter table task_queue add column if not exists tier text;
alter table task_queue add column if not exists substrate text;

comment on column task_queue.target_repo is
  'owner/repo this task targets (#1119). Backfilled to ''Osasuwu/jarvis'' for existing issue_number rows. NULL for legacy rows with no issue_number either.';
comment on column task_queue.target_type is
  'Target kind, e.g. ''issue'' (#1119). Backfilled to ''issue'' wherever issue_number is set.';
comment on column task_queue.target_number is
  'Structured pin number, e.g. GitHub issue number (#1119). Backfilled from issue_number. Supersedes issue_number as the CAS key; issue_number kept as a deprecated mirror (drop = follow-up slice).';
comment on column task_queue.target_branch is
  'Structured pin branch, when the target is branch-scoped (#1119). NULL when not applicable.';
comment on column task_queue.tier is
  'Sandcastle execution tier for this row/attempt (#1119). Nullable text, no check constraint -- slot names are operator config (config/sandcastle.yaml), not a fixed enum.';
comment on column task_queue.substrate is
  'Execution substrate for this row (#1119, decision c5e2e14a). Nullable text, no check constraint.';

update task_queue
  set target_type = 'issue', target_number = issue_number, target_repo = 'Osasuwu/jarvis'
  where issue_number is not null;

-- Historical parked rows predate the non-terminal semantics (decision
-- f24ad617) -- convert to failed before rebuilding the CAS index so they do
-- not silently occupy the new non-terminal active-index slot forever.
update task_queue
  set status = 'failed', completed_at = now()
  where status = 'parked';

drop index if exists idx_task_queue_issue_number_active;

-- Target-based CAS (#1119): parked is now non-terminal, so it joins
-- pending/claimed/running in the active-row set this index guards.
create unique index if not exists idx_task_queue_target_active
  on task_queue(target_repo, target_type, target_number)
  where status in ('pending', 'claimed', 'running', 'parked');
