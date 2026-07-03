-- Phase 5.3-δ (issue #445) — Backfill fok_judgments from legacy mirror
-- Any events with fok_verdict in payload but no corresponding fok_judgments row
-- are backfilled. This handles events created during β→γ window when dual-write
-- only fired from γ onward.

INSERT INTO fok_judgments (recall_event_id, query, project, verdict, confidence, rationale, judge_model, judge_version, judged_at)
SELECT
  e.id,
  COALESCE(e.payload->>'query', ''),
  COALESCE(e.payload->>'project', 'Osasuwu/jarvis'),
  COALESCE(e.payload->>'fok_verdict', 'unknown'),
  CAST(e.payload->>'fok_confidence' AS real),
  e.payload->>'fok_reason',
  COALESCE(e.payload->>'judge_model', 'claude-haiku-4-5-20251001'),
  COALESCE(e.payload->>'judge_version', '5.3-β'),
  COALESCE(CAST(e.payload->>'fok_judged_at' AS timestamptz), now())
FROM events e
WHERE e.event_type = 'memory_recall'
  AND e.payload ? 'fok_verdict'
  AND e.id NOT IN (SELECT recall_event_id FROM fok_judgments)
ON CONFLICT (recall_event_id) DO NOTHING;
