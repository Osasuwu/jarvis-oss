-- Jarvis Supabase Schema
--
-- Declarative mirror of the tables/functions/RLS policies that live in
-- supabase/migrations/. Not applied directly — schema.sql is aspirational
-- documentation of the intended shape (#326); migrations are what actually
-- execute against a database. Kept in sync by hand when a migration changes
-- one of the objects below.
--
-- The prior memory-stack tables (memories, memory_links, memory_review_queue,
-- episodes, known_unknowns, fok_judgments) and their RPCs were retired along
-- with the standalone memory/status/morning MCP servers (#1801). The
-- reactive-core agent stack (orchestrator, executor, wake_driver) that used
-- to read/write task_queue and events was itself demolished in #1802 — those
-- two tables plus task_outcomes have no live producer or consumer left in
-- this repo (kept here only as historical/aspirational shape, not active
-- schema; #1803 tracks whether to drop them outright). Tables that do still
-- have a live consumer: goals, comm_patterns (+ its watermark/sources),
-- credential_registry, audit_log, review_debt, driver_heartbeat.

-- =========================================================================
-- Goals table — Jarvis 2.0 Pillar 1: Goals & Strategic Context
-- =========================================================================

create table if not exists goals (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  project text,
  direction text,
  priority text not null default 'P1'
    check (priority in ('P0', 'P1', 'P2')),
  status text not null default 'active'
    check (status in ('active', 'achieved', 'paused', 'abandoned')),

  why text,
  success_criteria jsonb default '[]',

  deadline date,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  closed_at timestamptz,

  progress jsonb default '[]',
  progress_pct integer default 0
    check (progress_pct >= 0 and progress_pct <= 100),

  risks jsonb default '[]',
  owner_focus text,
  jarvis_focus text,

  parent_id uuid references goals(id) on delete set null,

  outcome text,
  lessons text
);

create index if not exists idx_goals_status on goals(status);
create index if not exists idx_goals_project on goals(project);
create index if not exists idx_goals_priority on goals(priority);
create index if not exists idx_goals_parent on goals(parent_id);

-- Auto-update updated_at
create or replace function update_goals_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists goals_updated_at on goals;
create trigger goals_updated_at
  before update on goals
  for each row execute function update_goals_updated_at();

-- RLS
alter table goals enable row level security;

create policy "Allow all for authenticated" on goals
  for all using (true) with check (true);

create policy "Allow all for anon" on goals
  for all to anon using (true) with check (true);


-- =========================================================================
-- Events table — Jarvis Pillar 2: Event-Driven Perception
-- GitHub Actions write events here, orchestrator reads them.
-- =========================================================================

create table if not exists events (
  id uuid primary key default gen_random_uuid(),

  -- Event classification
  event_type text not null,          -- 'ci_failure', 'security_alert', 'pr_approved', 'deployment', etc.
  severity text not null default 'info'
    check (severity in ('critical', 'high', 'medium', 'low', 'info')),

  -- Source
  repo text not null,                -- 'Osasuwu/jarvis', 'SergazyNarynov/redrobot', etc.
  source text not null default 'github_action',  -- 'github_action', 'webhook', 'manual'

  -- Content
  title text not null,               -- one-line summary
  payload jsonb default '{}',        -- structured event data (PR number, workflow name, alert details)

  -- Processing
  processed_at timestamptz,
  processed_by text,                 -- 'autonomous-loop', 'risk-radar', 'manual'
  action_taken text,                 -- what was done in response

  -- Event queue FSM (#739)
  state text not null default 'pending'
    check (state in ('pending', 'claimed', 'processed', 'parked')),
  -- sha256 of identifying fields. FULL unique constraint (NULLS DISTINCT), not a
  -- partial index: PostgREST's bare ON CONFLICT (dedup_key) cannot infer a
  -- partial unique index — every #953 emission died with 42P10 until #1491.
  dedup_key text unique,
  claimed_at timestamptz,
  claimed_by text,                   -- who claimed it (e.g. 'orchestrator', 'wake_driver')

  -- Timestamps
  created_at timestamptz default now(),
  event_at timestamptz default now() -- when the event actually occurred (may differ from insert time)
);

-- Indexes
create index if not exists idx_events_repo on events(repo);
create index if not exists idx_events_type on events(event_type);
create index if not exists idx_events_created on events(created_at desc);
-- dedup_key uniqueness lives on the column (constraint events_dedup_key_key);
-- the old partial idx_events_dedup_key was dropped by migration
-- events_dedup_key_full_unique_constraint (#1491).
create index if not exists idx_events_pending on events(state, severity, created_at)
  where state = 'pending';

-- RLS
alter table events enable row level security;

create policy "Allow all for authenticated" on events
  for all using (true) with check (true);

create policy "Allow all for anon" on events
  for all to anon using (true) with check (true);


-- =========================================================================
-- Event queue FSM: NOTIFY trigger + RPCs (#739)
-- =========================================================================

create or replace function notify_events_insert()
returns trigger as $$
begin
  perform pg_notify(
    'events',
    json_build_object(
      'id',         new.id,
      'event_type', new.event_type,
      'severity',   new.severity,
      'title',      new.title,
      'repo',       new.repo
    )::text
  );
  return new;
end;
$$ language plpgsql;

drop trigger if exists events_notify on events;
create trigger events_notify
  after insert on events
  for each row execute function notify_events_insert();

-- claim_next: atomically claim the highest-priority pending event
create or replace function claim_next(claimer text)
returns setof events
language plpgsql
as $$
declare
  event_row events%ROWTYPE;
begin
  select * into event_row
  from events
  where state = 'pending'
  order by
    case severity
      when 'critical' then 0
      when 'high'     then 1
      when 'medium'   then 2
      when 'low'      then 3
      when 'info'     then 4
    end asc,
    created_at asc
  limit 1
  for update skip locked;

  if found then
    update events
    set state = 'claimed',
        claimed_at = now(),
        claimed_by = claimer
    where id = event_row.id
    returning * into event_row;
    return next event_row;
  end if;
  return;
end;
$$;

-- mark_processed: transition claimed → processed
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

-- park_event: transition claimed → parked (blocked on dependency)
create or replace function park_event(
  event_id uuid,
  reason text default ''
)
returns boolean
language plpgsql
as $$
begin
  update events
  set state = 'parked',
      action_taken = park_event.reason
  where id = event_id and state = 'claimed';
  return found;
end;
$$;

-- requeue_event: transition claimed/parked → pending (retry)
create or replace function requeue_event(
  event_id uuid,
  reason text default ''
)
returns boolean
language plpgsql
as $$
begin
  update events
  set state = 'pending',
      claimed_at = null,
      claimed_by = null,
      action_taken = requeue_event.reason
  where id = event_id and (state = 'claimed' or state = 'parked');
  return found;
end;
$$;


-- =========================================================================
-- Task Outcomes — Pillar 3: Outcome Tracking & Learning
-- Records results of delegations, research, fixes, autonomous actions.
-- =========================================================================

create table if not exists task_outcomes (
  id uuid primary key default gen_random_uuid(),

  -- What was done
  task_type text not null
    check (task_type in ('delegation', 'research', 'fix', 'review', 'autonomous')),
  task_description text not null,
  outcome_status text not null default 'pending'
    check (outcome_status in ('pending', 'success', 'partial', 'failure', 'unknown')),
  outcome_summary text,

  -- Links
  goal_slug text,           -- related goal
  project text,             -- project scope
  issue_url text,           -- GitHub issue URL
  pr_url text,              -- GitHub PR URL

  -- Quality signals
  tests_passed boolean,
  pr_merged boolean,
  quality_score integer check (quality_score is null or (quality_score >= 0 and quality_score <= 100)),

  -- Learning
  lessons text,
  pattern_tags text[] default '{}',

  -- Verification
  verified_at timestamptz,  -- when outcome was verified (e.g. PR merged check)

  -- Timestamps
  created_at timestamptz default now(),

  -- Provenance prefix used by RLS to gate anon INSERT (slice 3, #542,
  -- decision 228a2d9b). Nullable for legacy rows; new sandcastle-agent
  -- writes must set 'sandcastle:<...>' to be accepted via anon key.
  source_provenance text
);

create index if not exists idx_task_outcomes_project on task_outcomes(project);
create index if not exists idx_task_outcomes_goal on task_outcomes(goal_slug);
create index if not exists idx_task_outcomes_status on task_outcomes(outcome_status);
create index if not exists idx_task_outcomes_created on task_outcomes(created_at desc);
create index if not exists idx_task_outcomes_tags on task_outcomes using gin(pattern_tags);

-- RLS
alter table task_outcomes enable row level security;

create policy "Allow all for authenticated" on task_outcomes
  for all using (true) with check (true);

-- Anon access — INSERT/UPDATE/DELETE gated by source_provenance prefix
-- (slices 3 + 3.5, #542 + #565).
create policy "Anon select" on task_outcomes
  for select to anon using (true);
create policy "Anon sandcastle update" on task_outcomes
  for update to anon
  using (source_provenance like 'sandcastle:%')
  with check (source_provenance like 'sandcastle:%');
create policy "Anon sandcastle delete" on task_outcomes
  for delete to anon
  using (source_provenance like 'sandcastle:%');
create policy "Anon sandcastle insert" on task_outcomes
  for insert to anon
  with check (source_provenance like 'sandcastle:%');

-- =========================================================================
-- Issue #740: Reshaped task_queue — drop approval columns, add
-- priority/assignee, new FSM.
--
-- FSM transitions (enforced in the interface; DB check guards the enum).
-- parked is non-terminal as of #1119 -- a parked row never spawns, but
-- unpark (transition(id, "pending")) resumes it through the normal drain:
--   pending  -> claimed
--   claimed  -> running | parked
--   running  -> done | failed | parked | skipped_duplicate
--   parked   -> pending
--   done, failed, skipped_duplicate -> (terminal)
--
-- Interface: agents/task_queue.py exposes enqueue(), claim_next()
-- (priority-ordered), and transition().
-- =========================================================================

create table if not exists task_queue (
  id uuid primary key default gen_random_uuid(),

  -- Intent
  goal text not null,
  scope_files text[] not null default '{}',

  -- Priority (higher = claimed first; FIFO ties) + optional worker assignee
  priority int not null default 0,
  assignee text,

  -- Lifecycle. `skipped_duplicate` (#931): dispatch-dedup terminal — issue
  -- already had a live PR or sibling row. Keep in lockstep with
  -- supabase/migrations/20260702120000_add_skipped_duplicate_status.sql.
  status text not null default 'pending'
    check (status in ('pending', 'claimed', 'running', 'done', 'failed', 'parked', 'skipped_duplicate')),
  claimed_at timestamptz,
  completed_at timestamptz,
  escalated_reason text,

  -- Dedup. sha256 hex (64 chars). Unique so a retrying worker cannot
  -- double-enqueue.
  idempotency_key text not null unique,

  -- Genuine GitHub issue target (#1085 S1-1). NULL for PR-target/no-target
  -- rows (review_negative, ci_failure) and legacy rows predating this
  -- column — those fall back to goal-regex extraction at the read sites.
  -- Keep in lockstep with
  -- supabase/migrations/20260811163000_add_task_queue_issue_number.sql.
  issue_number int,

  -- Plan-review drain gate (#1689): sha256 hex digest (agents.plan_lock.hash_plan)
  -- of the locked ## Plan section verified for this class:2 row. NULL for
  -- class:1/class:3 rows and legacy rows enqueued before this column existed.
  -- Keep in lockstep with
  -- supabase/migrations/20260825090000_add_task_queue_plan_digest.sql.
  plan_digest text,

  -- Replan-carrier gate (#1690): number of automatic replan cycles already
  -- spent on this row. 0 -> a replan-request comment triggers one automatic
  -- re-plan (incremented to 1); >=1 -> the next replan-request parks the row
  -- instead. Keep in lockstep with
  -- supabase/migrations/20260825100000_add_task_queue_replan_count.sql.
  replan_count integer not null default 0,

  -- Structured pins (#1119): supersede issue_number as the CAS key so a row
  -- can address any spawn target, not just a jarvis issue. Backfilled from
  -- issue_number; issue_number kept as a deprecated mirror (drop = follow-up
  -- slice). Nullable, no check constraints. Keep in lockstep with
  -- supabase/migrations/20260831120000_task_queue_pins_tier_substrate_parked_fsm.sql.
  target_repo text,
  target_type text,
  target_number int,
  target_branch text,

  -- Sandcastle execution tier for this row/attempt (#1119). Nullable text,
  -- no check constraint -- slot names are operator config
  -- (config/sandcastle.yaml), not a fixed enum.
  tier text,

  -- Execution substrate for this row (#1119, decision c5e2e14a). Nullable
  -- text, no check constraint.
  substrate text,

  -- Enqueue-path classifier (#1617): 'dispatch' for /dispatch (delegate:*)
  -- rows, 'orchestrator' for orchestrator-emitted rows (ci_failure/rework/
  -- global-task). Backfilled from idempotency_key prefix. NULL is
  -- fail-closed at drain time, not "no gate applies". Keep in lockstep with
  -- supabase/migrations/20260831130000_task_queue_origin.sql.
  origin text,

  -- Timestamps
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Dispatcher scan: highest-priority pending first, FIFO for ties.
create index if not exists idx_task_queue_pending_scan
  on task_queue(priority desc, created_at asc)
  where status = 'pending';

-- Target-based CAS (#1119): a unique index on (target_repo, target_type,
-- target_number) scoped to non-terminal rows means two rows targeting the
-- same spawn target cannot both be pending/claimed/running/parked at once.
-- parked joins this set because it is non-terminal as of #1119. Supersedes
-- idx_task_queue_issue_number_active (#1085 S1-1). Nullable-column unique
-- index allows unlimited NULLs, so untargeted/legacy rows never collide.
create unique index if not exists idx_task_queue_target_active
  on task_queue(target_repo, target_type, target_number)
  where status in ('pending', 'claimed', 'running', 'parked');

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

-- NOTIFY trigger on INSERT — wake signal for cap-freed dispatch (#922).
-- When a task reaches pending (insert or after cap-freed transition), fire
-- NOTIFY on 'task_queue' channel so wake_driver wakes immediately instead of
-- waiting for the idle-timeout watchdog.
create or replace function notify_task_queue_insert()
returns trigger as $$
begin
  perform pg_notify(
    'task_queue',
    json_build_object(
      'id',     new.id,
      'goal',   new.goal,
      'status', new.status
    )::text
  );
  return new;
end;
$$ language plpgsql;

drop trigger if exists task_queue_notify on task_queue;
create trigger task_queue_notify
  after insert on task_queue
  for each row execute function notify_task_queue_insert();

-- RLS -- matches the Pillar 7 convention (allow-all under service/anon
-- key; app-layer gatekeeping is the interface functions). Hardening
-- to per-role policies is its own sweep.
alter table task_queue enable row level security;

create policy "Allow all for authenticated" on task_queue
  for all using (true) with check (true);

create policy "Allow all for anon" on task_queue
  for all to anon using (true) with check (true);


-- =========================================================================
-- C17 events substrate (Sprint #35 / #476)
-- Canonical events table for all observability writes. See
-- docs/design/c17-events-substrate.md for the design 1-pager.
-- Existing `events` table (above) stays during cutover wave (jarvis-v2-
-- redesign.md:1566 two-mode coexistence).
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS pg_cron;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'event_outcome') THEN
    CREATE TYPE event_outcome AS ENUM ('success', 'failure', 'timeout', 'partial');
  END IF;
END$$;

CREATE TABLE IF NOT EXISTS events_canonical (
  event_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id        uuid NOT NULL,
  parent_event_id uuid NULL,
  ts              timestamptz NOT NULL DEFAULT now(),
  actor           text NOT NULL,
  action          text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  outcome         event_outcome NULL,
  cost_tokens     int NULL,
  cost_usd        numeric(12, 6) NULL,
  redacted        bool NOT NULL DEFAULT false,
  degraded        bool NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_events_canonical_trace_ts
  ON events_canonical (trace_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_canonical_actor_ts
  ON events_canonical (actor, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_canonical_action_ts
  ON events_canonical (action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_canonical_cost
  ON events_canonical (ts DESC)
  WHERE cost_usd IS NOT NULL;

CREATE OR REPLACE FUNCTION notify_events_canonical()
RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'events_canonical',
    json_build_object(
      'event_id', NEW.event_id,
      'trace_id', NEW.trace_id,
      'action',   NEW.action,
      'actor',    NEW.actor
    )::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_canonical_notify ON events_canonical;
CREATE TRIGGER events_canonical_notify
  AFTER INSERT ON events_canonical
  FOR EACH ROW EXECUTE FUNCTION notify_events_canonical();

ALTER TABLE events_canonical ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for authenticated" ON events_canonical
  FOR ALL USING (true) WITH CHECK (true);
-- Anon access — INSERT/UPDATE/DELETE gated on `actor` (provenance field).
-- Slices 3 + 3.5, #542 + #565.
CREATE POLICY "Anon select" ON events_canonical
  FOR SELECT TO anon USING (true);
CREATE POLICY "Anon sandcastle update" ON events_canonical
  FOR UPDATE TO anon
  USING (actor LIKE 'sandcastle:%')
  WITH CHECK (actor LIKE 'sandcastle:%');
CREATE POLICY "Anon sandcastle delete" ON events_canonical
  FOR DELETE TO anon
  USING (actor LIKE 'sandcastle:%');
CREATE POLICY "Anon sandcastle insert" ON events_canonical
  FOR INSERT TO anon
  WITH CHECK (actor LIKE 'sandcastle:%');

CREATE MATERIALIZED VIEW IF NOT EXISTS events_cost_by_day_mv AS
SELECT
  date_trunc('day', ts)                       AS day,
  actor,
  payload->>'gen_ai.request.model'            AS model,
  SUM(cost_tokens)                            AS total_tokens,
  SUM(cost_usd)                               AS total_usd,
  COUNT(*)                                    AS n_events
FROM events_canonical
WHERE cost_usd IS NOT NULL
  AND degraded = false
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_cost_by_day_mv_uniq
  ON events_cost_by_day_mv (day, actor, model);

CREATE MATERIALIZED VIEW IF NOT EXISTS events_last_run_by_actor_mv AS
SELECT
  actor,
  action,
  MAX(ts) FILTER (WHERE outcome = 'success') AS last_success_at,
  MAX(ts)                                    AS last_event_at,
  COUNT(*)                                   AS n_events
FROM events_canonical
GROUP BY actor, action
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_last_run_by_actor_mv_uniq
  ON events_last_run_by_actor_mv (actor, action);

-- pg_cron schedules registered in the migration file
-- (cron.schedule(...) is idempotent on (jobname); not duplicated here to
--  keep schema.sql declarative — the cron jobs live in cron.job).

-- =====================================================================
-- comm_patterns — communication-pattern instances (#580, ADR 0004)
-- One row per detected pattern instance. Stop-hook extractor classifies
-- user→assistant turns and writes here. Cross-device aggregate; no
-- project pinning (global scope per ADR 0004 §3).
-- =====================================================================

create table if not exists comm_patterns (
  id uuid default gen_random_uuid() primary key,

  -- Origin
  device text not null,
  session_id text not null,
  message_idx int not null,
  captured_at timestamptz not null,

  -- Classifier output
  primary_label text not null check (primary_label in (
    'correction_wrong_direction',
    'correction_incomplete',
    'affirmation',
    'affirmation_with_redirect',
    'preference_directive',
    'meta_protocol'
  )),
  subtype text,
  confidence numeric(3,2) not null check (confidence >= 0 and confidence <= 1),

  -- Anchor (raw text + redaction marker; ADR 0004 §2)
  anchor_quote text not null,
  redacted boolean not null default false,

  -- Day-1 embeddings (Voyage 512-dim, matches `memories.embedding`)
  embedding vector(512),

  -- Provenance + bookkeeping
  source_provenance text not null,
  created_at timestamptz default now()
);

create index if not exists idx_comm_patterns_label_captured
  on comm_patterns (primary_label, captured_at desc);

create unique index if not exists idx_comm_patterns_dedup
  on comm_patterns (device, session_id, message_idx);

create index if not exists idx_comm_patterns_no_embedding
  on comm_patterns (id) where embedding is null;

alter table comm_patterns enable row level security;

create policy "Allow all for authenticated" on comm_patterns
  for all using (true) with check (true);

-- Per-(device, session) Stop-hook watermark for idempotent extraction.
-- Extractor reads the watermark, only classifies messages with idx >
-- last_message_idx, then bumps the watermark in the same transaction.
create table if not exists comm_patterns_watermark (
  device text not null,
  session_id text not null,
  last_message_idx int not null default -1,
  updated_at timestamptz default now(),
  primary key (device, session_id)
);

alter table comm_patterns_watermark enable row level security;

create policy "Allow all for authenticated" on comm_patterns_watermark
  for all using (true) with check (true);


-- =========================================================================
-- Global Task Sources — Issue #679: AFK loop input registry
-- Non-repo, recurring tasks. Advancer ticks due rows into events queue.
-- =========================================================================

create table if not exists global_task_sources (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  body text,
  dispatcher_skill text not null
    check (dispatcher_skill in ('research', 'self-improve', 'status-record', 'last-work-report')),
  output_sink text not null
    -- 'event_reemit' is RESERVED / NOT-YET-IMPLEMENTED: the enum value exists so
    -- the dispatcher_sink_compatibility matrix below can already forbid the
    -- nonsensical pairings, but no advancer/orchestrator path consumes it yet
    -- (tracked under #679). Treat a row carrying it as inert until wired.
    check (output_sink in ('memory', 'telegram_digest', 'event_reemit')),
  payload jsonb default '{}',
  -- cadence floor of 1 second: the advancer divides by EXTRACT(EPOCH FROM
  -- cadence) when counting lapsed intervals, so a zero cadence is a
  -- divide-by-zero that aborts the whole advance transaction, and a sub-second
  -- cadence makes the int(epoch) dedup_key collide across ticks. NULL stays a
  -- valid one-shot.
  cadence interval check (cadence is null or cadence >= interval '1 second'),
  last_run timestamptz,
  next_run timestamptz,
  enabled bool not null default true,
  on_lapse text not null default 'coalesce'
    check (on_lapse in ('coalesce', 'fire_per_interval')),
  created_at timestamptz not null default now()
);

-- Dispatcher/sink compatibility matrix (enforced at DB level).
-- status-record -> memory only (it only ever writes a status memory row);
-- research -> any sink; self-improve / last-work-report -> any sink EXCEPT
-- event_reemit (re-emitting an event back into the queue from a self-improve
-- or report run would loop the advancer against its own output — neither skill
-- produces an event-shaped result, so the pairing is incoherent, not just
-- unimplemented).
-- drop-then-add (not the add-only DO/EXCEPTION guard the sibling constraints use):
-- this matrix was TIGHTENED to exclude event_reemit for self-improve /
-- last-work-report, so a DB carrying the earlier, looser constraint must have it
-- replaced. An add-only guard would hit duplicate_object and silently keep the
-- old, permissive form. DROP ... IF EXISTS is a no-op on a fresh DB.
alter table global_task_sources drop constraint if exists dispatcher_sink_compatibility;
alter table global_task_sources add constraint dispatcher_sink_compatibility
  check (
    (dispatcher_skill = 'status-record' and output_sink = 'memory') or
    (dispatcher_skill = 'research') or
    (dispatcher_skill = 'self-improve' and output_sink <> 'event_reemit') or
    (dispatcher_skill = 'last-work-report' and output_sink <> 'event_reemit')
  );

-- fire_per_interval needs a cadence to count intervals against; a one-shot
-- (cadence IS NULL) with fire_per_interval is an incoherent state. Forbid it.
do $$
begin
  alter table global_task_sources add constraint cadence_lapse_coherence
    check (not (cadence is null and on_lapse = 'fire_per_interval'));
exception when duplicate_object then null;
end $$;

-- Indexes for efficient due-row queries and enabled filtering.
create index if not exists idx_global_task_sources_enabled_next_run
  on global_task_sources(enabled, next_run)
  where enabled = true;

create index if not exists idx_global_task_sources_dispatcher
  on global_task_sources(dispatcher_skill);

-- RLS: writes are service-role only; anon gets read-only visibility.
-- service_role BYPASSES RLS, so the advancer (service DSN) needs no write
-- policy. No policy grants INSERT/UPDATE/DELETE, so RLS denies writes to anon
-- and authenticated alike. The previous "Allow all for authenticated" policy
-- used `for all using (true)` with no TO clause — which defaults to PUBLIC and
-- silently granted write to every authenticated JWT client. Dropped.
-- DROP ... IF EXISTS + CREATE keeps policy setup idempotent (Postgres has no
-- CREATE POLICY IF NOT EXISTS).
alter table global_task_sources enable row level security;

drop policy if exists "Allow all for authenticated" on global_task_sources;
drop policy if exists "Anon select only" on global_task_sources;
create policy "Anon select only" on global_task_sources
  for select to anon using (true);

-- ===========================================================================
-- credential_registry (Pillar 9): metadata inventory of credentials — service,
-- env-var NAME, storage location, rotation/expiry. NEVER secret values —
-- enforced below by the defense-in-depth CHECK constraint, not a dedicated
-- handler module. Applied to remote as migrations 20260415082814 (create) +
-- 20260708044124 (RLS); documented here per #326.
-- ===========================================================================
create table if not exists credential_registry (
  id uuid primary key default gen_random_uuid(),
  service text not null,
  env_var text not null unique,
  stored_in text not null default '.env',
  scope text not null default 'jarvis',
  created_at timestamptz default now(),
  expires_at timestamptz,
  last_rotated_at timestamptz,
  rotation_notes text,
  notes text,
  -- Defence-in-depth: reject rows whose metadata columns look like a raw secret.
  check (
    env_var !~ '^(eyJ|sk-|ghp_|ghs_|AKIA|xox[bpras]-)'
    and (rotation_notes is null or rotation_notes !~ '(eyJ|sk-|ghp_|ghs_|AKIA)')
    and (notes is null or notes !~ '(eyJ|sk-|ghp_|ghs_|AKIA)')
  )
);

-- RLS: allow-all convention (service_role bypasses). Enabled to clear the
-- Supabase rls_disabled_in_public ERROR; access is unchanged (app-layer trust).
alter table credential_registry enable row level security;
drop policy if exists "Allow all for authenticated" on credential_registry;
drop policy if exists "Allow all for anon" on credential_registry;
create policy "Allow all for authenticated" on credential_registry
  for all using (true) with check (true);
create policy "Allow all for anon" on credential_registry
  for all to anon using (true) with check (true);

-- ===========================================================================
-- audit_log: fire-and-forget trail of MCP tool invocations. Applied to remote
-- as migrations 20260415113317 (create) + 20260708044124 (RLS); documented
-- here per #326.
-- ===========================================================================
create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  "timestamp" timestamptz default now(),
  agent_id text,
  tool_name text not null,
  action text not null,
  target text,
  details jsonb default '{}'::jsonb,
  outcome text default 'success'
);

create index if not exists idx_audit_log_timestamp on audit_log ("timestamp" desc);
create index if not exists idx_audit_log_tool_name on audit_log (tool_name);

alter table audit_log enable row level security;
drop policy if exists "Allow all for authenticated" on audit_log;
drop policy if exists "Allow all for anon" on audit_log;
create policy "Allow all for authenticated" on audit_log
  for all using (true) with check (true);
create policy "Allow all for anon" on audit_log
  for all to anon using (true) with check (true);

-- ===========================================================================
-- review_debt: sub-MAJOR code-review findings, collected + clustered (#1211).
-- Applied to remote as migration 20260721120000_create_review_debt.sql;
-- documented here per #326 (schema.sql is aspirational; the migration executes).
-- The review-debt collector persists MEDIUM/INFO findings that never block a
-- merge, dedups by (module_area + rule + file), clusters by module_area, and
-- auto-files one review-debt-cluster issue at threshold.
-- ===========================================================================
create table if not exists review_debt (
  id            uuid primary key default gen_random_uuid(),
  dedup_key     text not null unique,   -- module_area + rule + file (no desc/line)
  module_area   text not null,          -- parent dir; the clustering bucket
  severity      text not null,          -- MEDIUM | INFO
  weight        numeric not null default 0.5,   -- per-severity contribution
  rule          text not null default '',
  file          text not null default '',
  seen_count    integer not null default 1,     -- raw occurrences (incremented)
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  issued_state  text not null default 'open_debt',  -- open_debt | clustered
  cluster_issue integer,                -- issue number once clustered
  source_pr     text
);

create index if not exists idx_review_debt_module_area on review_debt (module_area);
create index if not exists idx_review_debt_issued_state on review_debt (issued_state);
create index if not exists idx_review_debt_last_seen on review_debt (last_seen_at desc);

-- Upsert RPC increments seen_count on a repeat finding (merge-duplicates can't).
create or replace function review_debt_upsert(
  p_dedup_key text, p_module_area text, p_severity text, p_weight numeric,
  p_rule text, p_file text, p_source_pr text, p_seen_at timestamptz default now()
) returns review_debt language plpgsql security invoker set search_path = public as $$
declare result review_debt;
begin
  insert into review_debt as rd (
    dedup_key, module_area, severity, weight, rule, file,
    source_pr, first_seen_at, last_seen_at)
  values (p_dedup_key, p_module_area, p_severity, p_weight, p_rule, p_file,
    p_source_pr, p_seen_at, p_seen_at)
  on conflict (dedup_key) do update
    set seen_count = rd.seen_count + 1, last_seen_at = excluded.last_seen_at,
        source_pr = excluded.source_pr,
        issued_state = case when rd.issued_state = 'clustered' then 'clustered'
                            else 'open_debt' end
  returning rd.* into result;
  return result;
end; $$;

alter table review_debt enable row level security;
drop policy if exists "Allow all for authenticated" on review_debt;
drop policy if exists "Allow all for anon" on review_debt;
create policy "Allow all for authenticated" on review_debt
  for all using (true) with check (true);
create policy "Allow all for anon" on review_debt
  for all to anon using (true) with check (true);

-- ===========================================================================
-- scrubber_block_event_upsert: day-bucketed dedup + occurrence counter for
-- mcp_write_scrubber_block events (AC2, #1000).
-- Applied to remote as migration 20260809180000_create_scrubber_block_event_upsert.sql,
-- fixed by 20260810130000_fix_scrubber_upsert_onconflict_full_constraint.sql (#1498);
-- documented here per #326 (schema.sql is aspirational; the migration executes).
-- No new table — this adds an RPC over the existing `events` table (see the
-- events block above) so write_scrubber.py's log_block_event() can upsert
-- against events.dedup_key's FULL unique constraint (events_dedup_key_key)
-- instead of inserting a new row per repeat fire on a hot blocked-write path.
-- events.payload has no dedicated counter column (unlike review_debt.seen_count
-- above), so the increment happens via jsonb_set/coalesce on
-- payload.occurrence_count.
-- ===========================================================================
create or replace function scrubber_block_event_upsert(
  p_dedup_key   text,
  p_severity    text,
  p_repo        text,
  p_write_path  text,
  p_patterns    jsonb,
  p_seen_at     timestamptz default now()
) returns events language plpgsql security invoker set search_path = public as $$
declare result events;
begin
  insert into events as e (
    event_type, severity, repo, source, title, payload, dedup_key, event_at
  )
  values (
    'mcp_write_scrubber_block', p_severity, p_repo, 'mcp_memory',
    'Write blocked by secret scrubber (' || p_write_path || ')',
    jsonb_build_object(
      'write_path', p_write_path, 'patterns', p_patterns, 'occurrence_count', 1
    ),
    p_dedup_key, p_seen_at
  )
  -- events.dedup_key is a FULL unique constraint (events_dedup_key_key, #1491)
  -- — a plain conflict target matches it directly; a WHERE-qualified target
  -- (the #1000 original shape) only matches a *partial* index and 42P10s
  -- against a full constraint (#1498).
  on conflict (dedup_key) do update
    set payload = jsonb_set(
          e.payload, '{occurrence_count}',
          to_jsonb(coalesce((e.payload->>'occurrence_count')::int, 1) + 1)
        ),
        event_at = excluded.event_at
  returning e.* into result;
  return result;
end; $$;

-- ===========================================================================
-- scrubber_disabled_event_upsert: day-bucketed dedup + occurrence counter for
-- mcp_write_scrubber_disabled events (AC1, #1000 — code-review round-2 fix).
-- Applied to remote as migration 20260810120000_create_scrubber_disabled_event_upsert.sql,
-- fixed by 20260810130000_fix_scrubber_upsert_onconflict_full_constraint.sql (#1498);
-- documented here per #326 (schema.sql is aspirational; the migration executes).
-- Mirrors scrubber_block_event_upsert above exactly. The original
-- log_disabled_event() called a plain `.table("events").upsert(...,
-- on_conflict="dedup_key")`, which could not satisfy events.dedup_key's then-
-- PARTIAL unique index — Postgres only infers a partial index as the ON
-- CONFLICT arbiter when its predicate is restated in the conflict target, so
-- every upsert raised and was silently swallowed by the surrounding
-- except-and-log-type-only handler. No disabled-gate event ever landed.
-- ===========================================================================
create or replace function scrubber_disabled_event_upsert(
  p_dedup_key  text,
  p_reason     text,
  p_repo       text,
  p_seen_at    timestamptz default now()
) returns events language plpgsql security invoker set search_path = public as $$
declare result events;
begin
  insert into events as e (
    event_type, severity, repo, source, title, payload, dedup_key, event_at
  )
  values (
    'mcp_write_scrubber_disabled', 'high', p_repo, 'mcp_memory',
    'Tier-2 write-scrubber gate is disabled',
    jsonb_build_object('reason', p_reason, 'occurrence_count', 1),
    p_dedup_key, p_seen_at
  )
  -- events.dedup_key is a FULL unique constraint (events_dedup_key_key, #1491)
  -- — see scrubber_block_event_upsert above for why the conflict target must
  -- stay unqualified (#1498).
  on conflict (dedup_key) do update
    set payload = jsonb_set(
          e.payload, '{occurrence_count}',
          to_jsonb(coalesce((e.payload->>'occurrence_count')::int, 1) + 1)
        ),
        event_at = excluded.event_at
  returning e.* into result;
  return result;
end; $$;

-- ===========================================================================
-- driver_heartbeat: cross-device liveness signal for wake_driver (#1085).
-- Applied to remote as migration 20260812120000_create_driver_heartbeat.sql;
-- documented here per #326 (schema.sql is aspirational; the migration executes).
-- The WRITE (wake_driver stamping its own tick) ships in Slice 3 (S3-1); the
-- table ships in Slice 2 because /dispatch's post-enqueue READ (S2-6) needs
-- real storage to query against. No row (pre-Slice-3, or never ticked) and a
-- stale last_tick are both legitimately "stale" to any reader.
-- ceiling: single-row usage (driver_name='wake_driver') — see migration.
-- ===========================================================================
create table if not exists driver_heartbeat (
  driver_name text primary key,
  last_tick   timestamptz not null
);

alter table driver_heartbeat enable row level security;
drop policy if exists "Allow all for authenticated" on driver_heartbeat;
drop policy if exists "Allow all for anon" on driver_heartbeat;
create policy "Allow all for authenticated" on driver_heartbeat
  for all using (true) with check (true);
create policy "Allow all for anon" on driver_heartbeat
  for all to anon using (true) with check (true);
