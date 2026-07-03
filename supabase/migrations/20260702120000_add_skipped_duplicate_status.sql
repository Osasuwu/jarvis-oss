-- #931 dispatch-dedup: add `skipped_duplicate` terminal status to task_queue.
-- A running row whose issue turns out to already have a live PR (or a live
-- sibling queue row) terminates as skipped_duplicate — no requeue, no retry.
-- Keep the status list in lockstep with mcp-memory/schema.sql and
-- agents/task_queue.py (_VALID_TRANSITIONS / _TERMINAL_STATES).

alter table task_queue drop constraint task_queue_status_check;
alter table task_queue add constraint task_queue_status_check
  check (status in ('pending', 'claimed', 'running', 'done', 'failed', 'parked', 'skipped_duplicate'));
