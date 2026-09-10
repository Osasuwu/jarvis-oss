-- #1617 (M58): distinct drain-time readiness gate for orchestrator-emitted
-- task rows. `origin` classifies which enqueue path produced a row so
-- drain_tasks can route it to the right readiness gate, retiring the
-- idempotency_key.startswith("delegate:") sniff.
-- Keep in lockstep with mcp-memory/schema.sql.

alter table task_queue add column if not exists origin text;

comment on column task_queue.origin is
  'Enqueue-path classifier (#1617): ''dispatch'' for /dispatch (delegate:*) rows, ''orchestrator'' for orchestrator-emitted rows (ci_failure/rework/global-task). Backfilled from idempotency_key prefix. NULL is fail-closed at drain time, not "no gate applies".';

update task_queue
  set origin = case when idempotency_key like 'delegate:%' then 'dispatch' else 'orchestrator' end
  where origin is null;
