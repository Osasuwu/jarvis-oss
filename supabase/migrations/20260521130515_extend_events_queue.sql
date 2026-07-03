-- Extend the live `events` table with a state machine, deduplication, a wake
-- signal, and a clean starting point. Part of event_queue substrate (#739).
--
-- Design: milestone #44 "Reactive-core: event-woken orchestrator + durable
-- queues" — decision `2c5384d0` (substrate split: extend live events, reshape
-- empty task_queue, retire LangGraph/APScheduler).
--
-- The events table is live and producing (~2674 rows), fed by
-- event-dispatch.yml. This migration extends it in place rather than creating
-- a new table — the consumer died 2026-05-04 and this is the resurrection.

-- =========================================================================
-- 1. New columns: state machine, dedup, claiming
-- =========================================================================

ALTER TABLE events ADD COLUMN IF NOT EXISTS state text
  NOT NULL DEFAULT 'pending'
  CHECK (state IN ('pending', 'claimed', 'processed', 'parked'));

ALTER TABLE events ADD COLUMN IF NOT EXISTS dedup_key text;

-- Unique constraint allows multiple NULLs (Postgres treats NULL != NULL), so
-- existing rows without a dedup_key are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup_key
  ON events (dedup_key)
  WHERE dedup_key IS NOT NULL;

ALTER TABLE events ADD COLUMN IF NOT EXISTS claimed_at timestamptz;
ALTER TABLE events ADD COLUMN IF NOT EXISTS claimed_by text;

COMMENT ON COLUMN events.state IS
  'FSM: pending → claimed → processed | parked. pending → parked for start-clean.';

COMMENT ON COLUMN events.dedup_key IS
  'sha256 of identifying fields. NULL for legacy rows; unique when set.';

-- =========================================================================
-- 2. Backfill: legacy columns → new state
-- =========================================================================

-- Rows already marked processed in the old column → processed state.
UPDATE events SET state = 'processed' WHERE processed = true AND state = 'pending';

-- =========================================================================
-- 3. Start-clean: archive all existing unprocessed rows as processed
-- =========================================================================

-- The ~2674 backlog rows (mostly stale CI noise since 2026-05-04) are set to
-- processed so the new orchestrator starts on an empty pending queue rather
-- than replaying old noise.
UPDATE events SET state = 'processed' WHERE state = 'pending';

-- =========================================================================
-- 4. NOTIFY trigger on INSERT — wake signal for LISTEN clients
-- =========================================================================

CREATE OR REPLACE FUNCTION notify_events_insert()
RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'events',
    json_build_object(
      'id',         NEW.id,
      'event_type', NEW.event_type,
      'severity',   NEW.severity,
      'title',      NEW.title,
      'repo',       NEW.repo
    )::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_notify ON events;
CREATE TRIGGER events_notify
  AFTER INSERT ON events
  FOR EACH ROW EXECUTE FUNCTION notify_events_insert();

-- =========================================================================
-- 5. RPC: claim_next — atomically claim highest-priority pending event
-- =========================================================================

CREATE OR REPLACE FUNCTION claim_next(claimer text)
RETURNS SETOF events
LANGUAGE plpgsql
AS $$
DECLARE
  event_row events%ROWTYPE;
BEGIN
  SELECT * INTO event_row
  FROM events
  WHERE state = 'pending'
  ORDER BY
    CASE severity
      WHEN 'critical' THEN 0
      WHEN 'high'     THEN 1
      WHEN 'medium'   THEN 2
      WHEN 'low'      THEN 3
      WHEN 'info'     THEN 4
    END ASC,
    created_at ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED;

  IF FOUND THEN
    UPDATE events
    SET state = 'claimed',
        claimed_at = now(),
        claimed_by = claimer
    WHERE id = event_row.id
    RETURNING * INTO event_row;
    RETURN NEXT event_row;
  END IF;
  RETURN;
END;
$$;

COMMENT ON FUNCTION claim_next IS
  'Claim the highest-severity pending event. Returns the row or empty set if none pending.';

-- =========================================================================
-- 6. RPC: mark_processed — transition claimed → processed
-- =========================================================================

CREATE OR REPLACE FUNCTION mark_processed(
  event_id uuid,
  processor text,
  action_taken text DEFAULT ''
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE events
  SET state = 'processed',
      processed = true,
      processed_at = now(),
      processed_by = processor,
      action_taken = mark_processed.action_taken
  WHERE id = event_id AND state = 'claimed';
  RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION mark_processed IS
  'Transition a claimed event to processed. Returns true if a row was updated.';

-- =========================================================================
-- 7. RPC: park_event — transition claimed → parked (blocked by dependency)
-- =========================================================================

CREATE OR REPLACE FUNCTION park_event(
  event_id uuid,
  reason text DEFAULT ''
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE events
  SET state = 'parked',
      action_taken = park_event.reason
  WHERE id = event_id AND state = 'claimed';
  RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION park_event IS
  'Park a claimed event that is blocked on a dependency. Returns true if updated.';

-- =========================================================================
-- 8. RPC: requeue_event — transition parked/claimed → pending (retry)
-- =========================================================================

CREATE OR REPLACE FUNCTION requeue_event(
  event_id uuid,
  reason text DEFAULT ''
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE events
  SET state = 'pending',
      claimed_at = NULL,
      claimed_by = NULL,
      action_taken = requeue_event.reason
  WHERE id = event_id AND (state = 'claimed' OR state = 'parked');
  RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION requeue_event IS
  'Re-queue a claimed or parked event back to pending. Returns true if updated.';
