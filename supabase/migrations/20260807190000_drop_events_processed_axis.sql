-- Decommission the legacy events.processed boolean axis. #1391.
--
-- The FSM `state` column (pending|claimed|processed|parked) has been the
-- authoritative lifecycle tracker since #739. Keeping the boolean alive
-- forced every query to know which axis was current. The Orchestrator-
-- Watcher that used to read it is gone (superseded by wake_driver,
-- #1384/#1386) and no remaining code path reads or writes the column.
--
-- No backfill: the ~11,995 rows still carrying processed=false are dropped
-- with the column. Backfilling a column that's being removed in the same
-- migration produces nothing anyone reads.

drop index if exists idx_events_unprocessed;

alter table events drop column if exists processed;

create or replace function mark_processed(
  event_id uuid,
  processor text,
  action_taken text default ''
)
returns boolean
language plpgsql
as $$
begin
  update events
  set state = 'processed',
      processed_at = now(),
      processed_by = processor,
      action_taken = mark_processed.action_taken
  where id = event_id and state = 'claimed';
  return found;
end;
$$;
