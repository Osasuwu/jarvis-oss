-- #1085 Slice 1 (S1-1): structured issue_number column on task_queue, plus a
-- partial unique index scoped to non-terminal rows. This is the per-issue CAS
-- that replaces the branch-push claim (deleted in Slice 2) — two rows
-- targeting the same issue while either is still pending/claimed/running
-- cannot both exist, closing the manual-/delegate-path gap that #931's
-- prose-invoked predicate could not (decision 6c55cf0a, #1085).
--
-- Nullable + additive: legacy/orchestrator rows with no genuine issue target
-- (review_negative, ci_failure) stay NULL by design (decision e89bb95e) and a
-- unique index on a nullable column allows unlimited NULLs, so they never
-- collide with each other. Keep in lockstep with mcp-memory/schema.sql.

alter table task_queue add column if not exists issue_number int null;

comment on column task_queue.issue_number is
  'GitHub issue this task targets, when genuinely issue-scoped (#1085 S1-1). NULL for PR-target/no-target rows (review_negative, ci_failure) and legacy rows enqueued before this column existed — those fall back to goal-regex extraction (_goal_issue_number) at the read sites.';

-- Non-terminal = pending, claimed, running (task_queue_status_check literal
-- list minus done/failed/parked/skipped_duplicate). A terminal row's
-- issue_number is irrelevant to in-flight dedup, so it is excluded from the
-- index rather than the row being required to have issue_number null.
create unique index if not exists idx_task_queue_issue_number_active
  on task_queue(issue_number)
  where status in ('pending', 'claimed', 'running');
