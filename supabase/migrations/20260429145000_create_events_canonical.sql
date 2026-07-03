-- C17 events substrate (Sprint #35, issue #476)
-- Creates events_canonical: one append-only table for all observability writes.
-- Design 1-pager: docs/design/c17-events-substrate.md.
-- Paired with mcp-memory/schema.sql per CI gate (#326).
--
-- This migration is the SUBSTRATE only. No application writers are wired to it
-- yet (#477 wires record_decision; rest of writers come in substrate-consumer
-- wave). Legacy `events` table stays untouched during cutover (line 1566 of
-- jarvis-v2-redesign.md two-mode coexistence).

-- pg_cron is required for materialized-view refresh schedules below.
-- Available but not installed by default on Supabase.
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- =========================================================================
-- 1. Outcome enum
-- =========================================================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'event_outcome') THEN
    CREATE TYPE event_outcome AS ENUM ('success', 'failure', 'timeout', 'partial');
  END IF;
END$$;

-- =========================================================================
-- 2. events_canonical table
-- =========================================================================
CREATE TABLE IF NOT EXISTS events_canonical (
  event_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Trace propagation (OTel-style)
  trace_id        uuid NOT NULL,
  parent_event_id uuid NULL,

  -- Time + actor + action
  ts              timestamptz NOT NULL DEFAULT now(),
  actor           text NOT NULL,
  action          text NOT NULL,

  -- Type-specific payload (OTel GenAI keys verbatim when applicable —
  -- see docs/design/c17-events-substrate.md §2)
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- Outcome (NULL allowed for non-binary events like episode_started)
  outcome         event_outcome NULL,

  -- Inline cost (per L2 — no separate ledger table)
  cost_tokens     int NULL,
  cost_usd        numeric(12, 6) NULL,

  -- Hygiene flags
  redacted        bool NOT NULL DEFAULT false,
  degraded        bool NOT NULL DEFAULT false  -- replayed from in-memory buffer (#477)
);

COMMENT ON TABLE events_canonical IS
  'C17 canonical events substrate. All observability writes go here. '
  'Other observability tables (task_outcomes, episodes, audit_log, '
  'known_unknowns) become views over this in the cutover wave.';

COMMENT ON COLUMN events_canonical.trace_id IS
  'Groups all events from one initiating context (owner message, scheduled '
  'fire, subagent dispatch). Set by writer via contextvars.';

COMMENT ON COLUMN events_canonical.parent_event_id IS
  'Nesting — subagent events point to spawning event (typically a tool_call '
  'with gen_ai.tool.name=Agent). NULL for trace roots.';

COMMENT ON COLUMN events_canonical.degraded IS
  'True when this row was replayed from a writer-side in-memory buffer after '
  'a transient pg outage (per design §4). Exclude from cost reconciliation.';

-- =========================================================================
-- 3. Indexes
-- =========================================================================

-- Trace replay: WHERE trace_id = ? ORDER BY ts.
CREATE INDEX IF NOT EXISTS idx_events_canonical_trace_ts
  ON events_canonical (trace_id, ts);

-- Last-run-by-actor lookups, owner-facing dashboards.
CREATE INDEX IF NOT EXISTS idx_events_canonical_actor_ts
  ON events_canonical (actor, ts DESC);

-- Action-filtered queries (e.g., all decision_made events).
CREATE INDEX IF NOT EXISTS idx_events_canonical_action_ts
  ON events_canonical (action, ts DESC);

-- Cost rollups skip the bulk of cost-free events.
CREATE INDEX IF NOT EXISTS idx_events_canonical_cost
  ON events_canonical (ts DESC)
  WHERE cost_usd IS NOT NULL;

-- =========================================================================
-- 4. pg_notify trigger
-- =========================================================================
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

-- =========================================================================
-- 5. RLS — matches existing convention (allow all authenticated + anon).
--    Per-role hardening is a separate cross-schema sweep.
-- =========================================================================
ALTER TABLE events_canonical ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for authenticated" ON events_canonical
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for anon" ON events_canonical
  FOR ALL TO anon USING (true) WITH CHECK (true);

-- =========================================================================
-- 6. Materialized views — hot read paths from day one.
--    Per design L3 lean (line 1734 of jarvis-v2-redesign.md):
--    "single-canonical-events table needs read-side projections to avoid
--     read-amplification."
-- =========================================================================

-- Cost by day, actor, model. Read by /status, C13 cost ledger view (#37 next sprint).
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
  AND degraded = false  -- exclude replayed events from cost truth
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_cost_by_day_mv_uniq
  ON events_cost_by_day_mv (day, actor, model);

-- Last successful run per (actor, action). Replaces *_last_run memory abuse.
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

-- =========================================================================
-- 7. pg_cron schedules for materialized view refresh.
--    CONCURRENTLY requires the unique indexes above (already created).
-- =========================================================================
SELECT cron.schedule(
  'events_cost_by_day_mv_refresh',
  '0 * * * *',  -- hourly
  $$REFRESH MATERIALIZED VIEW CONCURRENTLY events_cost_by_day_mv$$
);

SELECT cron.schedule(
  'events_last_run_by_actor_mv_refresh',
  '*/5 * * * *',  -- every 5 minutes
  $$REFRESH MATERIALIZED VIEW CONCURRENTLY events_last_run_by_actor_mv$$
);
