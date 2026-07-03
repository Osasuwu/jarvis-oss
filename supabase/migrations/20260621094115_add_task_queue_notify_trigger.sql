-- Issue #922: Add NOTIFY trigger on task_queue insert for cap-freed dispatch latency.
--
-- When a task reaches ``pending`` status (fresh insert or after a cap-freed
-- transition), fire a NOTIFY on a distinct channel so wake_driver can wake
-- immediately instead of waiting for the idle-timeout watchdog.
--
-- Mirrors the pattern from #739's notify_events_insert trigger: AFTER INSERT
-- NOTIFY on task_queue channel (TASK_QUEUE_CHANNEL = "task_queue").
--
-- Decision: 2489782f-cd76-48db-87ba-5ad949e0623b (#909 dispatch-loop grill).

-- =========================================================================
-- NOTIFY trigger on task_queue INSERT — wake signal for LISTEN clients
-- =========================================================================

CREATE OR REPLACE FUNCTION notify_task_queue_insert()
RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'task_queue',
    json_build_object(
      'id',     NEW.id,
      'goal',   NEW.goal,
      'status', NEW.status
    )::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS task_queue_notify ON task_queue;
CREATE TRIGGER task_queue_notify
  AFTER INSERT ON task_queue
  FOR EACH ROW EXECUTE FUNCTION notify_task_queue_insert();
