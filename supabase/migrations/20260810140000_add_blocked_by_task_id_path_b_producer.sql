-- #1455 Path B producer: dedicated blocked_by_task_id column + atomic parking RPC.
--
-- Carrier decision (a4f6c602): a dedicated nullable column, NOT payload
-- mutation — the payload stays immutable so the task_queue idempotency key
-- (sha256 over the whole payload) is unchanged when the poller requeues the
-- event, and the replayed drain closes Path B via an enqueue collision.
-- Additive + nullable: shared-schema safe for redrobot consumers.

-- =========================================================================
-- 1. Column: events.blocked_by_task_id
-- =========================================================================

ALTER TABLE events ADD COLUMN IF NOT EXISTS blocked_by_task_id uuid NULL;

COMMENT ON COLUMN events.blocked_by_task_id IS
  'Task the event is parked blocked on (#1455 Path B producer). NULL for poison-pill parks (#1385) — the poller requeues only rows where this is set. Deliberately NOT cleared on requeue/close: lineage.';

-- =========================================================================
-- 2. RPC: park_blocked_on_task — claimed → parked, blocked on a task
-- =========================================================================
-- Sibling of park_event (20260521130515), which stays untouched for the
-- poison-pill path: that one must leave blocked_by_task_id NULL so crashed
-- events stay parked until a human intervenes.

CREATE OR REPLACE FUNCTION park_blocked_on_task(
  event_id uuid,
  task_id uuid,
  reason text DEFAULT ''
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE events
  SET state = 'parked',
      action_taken = park_blocked_on_task.reason,
      blocked_by_task_id = park_blocked_on_task.task_id
  WHERE id = event_id AND state = 'claimed';
  RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION park_blocked_on_task IS
  'Atomically park a claimed event blocked on a task: state=parked + blocked_by_task_id set in one UPDATE (#1455 Path B producer). Returns true if a row was updated.';
