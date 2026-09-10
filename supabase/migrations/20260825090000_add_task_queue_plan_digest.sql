-- #1689: plan-review drain gate — persist the locked plan's hash onto the
-- claiming task_queue row. Written once a class:2 row's planner-produced
-- plan is locked and verified (agents.plan_review_drain.write_plan_section
-- + set_plan_digest), and re-checked immediately before spawn (fail closed
-- if the issue body's current lock no longer matches — AC6).
--
-- Nullable + additive: class:1/class:3 rows and legacy rows enqueued before
-- this column existed never populate it. Not itself an FSM transition, so no
-- FSM validation gates the UPDATE (mirrors reclaim_stale_claimed/
-- requeue_running's direct-UPDATE pattern in agents/task_queue.py). Keep in
-- lockstep with mcp-memory/schema.sql.

alter table task_queue add column if not exists plan_digest text null;

comment on column task_queue.plan_digest is
  'sha256 hex digest (agents.plan_lock.hash_plan) of the locked ## Plan section verified for this class:2 row (#1689). NULL for class:1/class:3 rows and legacy rows enqueued before this column existed.';
