-- Pillar 4 / Memory FOK Phase 5.3-β (issue #443)
-- Canonical FOK (feeling-of-knowing) judgments store. Replaces the
-- events.payload.fok_verdict legacy mirror (dropped in 5.3-δ).
-- Paired with mcp-memory/schema.sql per CI gate (#326 / #289).
-- RLS + allow-all policies live in a follow-up migration to keep this
-- file scoped to the schema decision recorded in issue #443.

CREATE TABLE IF NOT EXISTS fok_judgments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recall_event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  query           text NOT NULL,
  project         text,
  verdict         text NOT NULL CHECK (verdict IN ('sufficient','partial','insufficient','unknown','skipped')),
  confidence      real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  rationale       text,
  judge_model     text NOT NULL,
  judge_version   text NOT NULL,
  judged_at       timestamptz NOT NULL DEFAULT now(),
  action_taken    text CHECK (action_taken IN ('pass_through','gap_recorded','widened') OR action_taken IS NULL),
  action_at       timestamptz,
  outcome_id      uuid REFERENCES task_outcomes(id) ON DELETE SET NULL,
  outcome_correct boolean,
  UNIQUE (recall_event_id)
);

CREATE INDEX IF NOT EXISTS idx_fok_judgments_verdict ON fok_judgments(verdict, judged_at DESC);
CREATE INDEX IF NOT EXISTS idx_fok_judgments_query_project ON fok_judgments(project, query);
CREATE INDEX IF NOT EXISTS idx_fok_judgments_outcome ON fok_judgments(outcome_id) WHERE outcome_id IS NOT NULL;
