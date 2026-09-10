alter table task_queue add column if not exists replan_count integer not null default 0;
comment on column task_queue.replan_count is
  'Replan-carrier gate (#1690): number of automatic replan cycles already spent on this row. 0 -> a replan-request comment triggers one automatic re-plan (incremented to 1); >=1 -> the next replan-request parks the row instead. Defaults to zero for legacy rows.';
