-- Pillar 7 Sprint 2: task_queue (issue #296, S2-1)
-- Dispatcher input surface. Paired with mcp-memory/schema.sql.
-- CI gate (#289) requires a migration file whenever schema.sql changes.

-- FSM transitions (enforced in the dispatcher; DB check guards the enum):
--   pending    -> dispatched | escalated | rejected
--   dispatched -> done | escalated
--   escalated  -> pending      (owner re-approves)
--   done       -> (terminal)
--   rejected   -> (terminal)

create table if not exists task_queue (
  id uuid primary key default gen_random_uuid(),

  -- Intent
  goal text not null,
  scope_files text[] not null default '{}',

  -- Approval (inputs for drift detection + auto-dispatch gating)
  approved_at timestamptz not null default now(),
  approved_by text not null,
  approved_scope_hash text not null,
  auto_dispatch boolean not null default false,

  -- Lifecycle
  status text not null default 'pending'
    check (status in ('pending', 'dispatched', 'done', 'escalated', 'rejected')),
  dispatched_at timestamptz,
  completed_at timestamptz,
  escalated_reason text,

  -- Dedup. Matches the safety-gate idempotency_key shape (sha256 hex, 64
  -- chars). Unique so a retrying dispatcher cannot double-enqueue.
  idempotency_key text not null unique,

  -- Timestamps
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Dispatcher scan: oldest approved pending first. Also covers 'dispatched'
-- so a restarted dispatcher can find in-flight rows to reconcile.
create index if not exists idx_task_queue_pending_scan
  on task_queue(status, approved_at)
  where status in ('pending', 'dispatched');

-- Dedicated updated_at trigger. The memories-shared update_updated_at()
-- references last_accessed_at/fts/project_key -- columns task_queue does
-- not have, so reusing it would error at runtime.
create or replace function update_task_queue_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists task_queue_updated_at on task_queue;
create trigger task_queue_updated_at
  before update on task_queue
  for each row execute function update_task_queue_updated_at();

-- RLS -- matches the Pillar 7 convention (allow-all under service/anon
-- key; app-layer gatekeeping is the safety gate + dispatcher). Hardening
-- to per-role policies is its own sweep.
alter table task_queue enable row level security;

create policy "Allow all for authenticated" on task_queue
  for all using (true) with check (true);

create policy "Allow all for anon" on task_queue
  for all to anon using (true) with check (true);
