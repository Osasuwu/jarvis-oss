-- Issue #740: Reshape task_queue — drop approval columns, add priority/assignee, new FSM
-- Paired with mcp-memory/schema.sql.
-- CI gate (#289) requires a migration file whenever schema.sql changes.

-- FSM transitions (enforced in the interface; DB check guards the enum):
--   pending  -> claimed
--   claimed  -> running
--   running  -> done | failed | parked
--   done     -> (terminal)
--   failed   -> (terminal)
--   parked   -> (terminal)

-- Drop old partial index (references old status values + approved_at)
drop index if exists idx_task_queue_pending_scan;

-- Drop approval-gated columns
alter table task_queue drop column approved_at;
alter table task_queue drop column approved_by;
alter table task_queue drop column approved_scope_hash;
alter table task_queue drop column auto_dispatch;

-- Rename dispatched_at to claimed_at for new FSM semantics
alter table task_queue rename column dispatched_at to claimed_at;

-- Add priority (for claim_next ordering) and assignee (for worker routing)
alter table task_queue add column priority int not null default 0;
alter table task_queue add column assignee text;

-- Remove smoke/test rows — no real data to preserve (per issue #740 spec)
delete from task_queue;

-- Replace status check constraint with new FSM
alter table task_queue drop constraint task_queue_status_check;
alter table task_queue add constraint task_queue_status_check
  check (status in ('pending', 'claimed', 'running', 'done', 'failed', 'parked'));

-- Partial index: highest-priority pending tasks first, FIFO for ties
create index if not exists idx_task_queue_pending_scan
  on task_queue(priority desc, created_at asc)
  where status = 'pending';
