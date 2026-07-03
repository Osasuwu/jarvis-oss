-- Phase 5.3-δ (issue #445) — FOK calibration summary RPC
-- Computes Brier score (mean squared error) of FOK verdicts against task outcomes
-- for confidence calibration analysis.
--
-- verdict_score mapping: sufficient=1.0, partial=0.5, insufficient=0.0, unknown=NULL
-- outcome_score mapping: success=1.0, partial=0.5, failure=0.0, unknown=NULL
--   (verify task_outcomes.outcome_status actual enum values in schema.sql line ~335)
--
-- brier = mean((verdict_score - outcome_score)^2) over joined rows
-- drift_signal = true if (brier >= 0.25 AND n >= 30); false if n < 30
-- NULL scores excluded from n and brier computation.

CREATE OR REPLACE FUNCTION fok_calibration_summary(p_project text DEFAULT NULL)
RETURNS TABLE (
  n integer,
  brier numeric,
  by_verdict json,
  drift_signal boolean
)
LANGUAGE sql STABLE
AS $$
WITH judgments_with_scores AS (
  -- Map fok_judgments.verdict → numeric score
  SELECT
    fj.id,
    fj.verdict,
    CASE fj.verdict
      WHEN 'sufficient' THEN 1.0
      WHEN 'partial' THEN 0.5
      WHEN 'insufficient' THEN 0.0
      WHEN 'unknown' THEN NULL
      WHEN 'skipped' THEN NULL
      ELSE NULL
    END AS verdict_score,
    -- Map task_outcomes.outcome_status → numeric score
    CASE tout.outcome_status
      WHEN 'success' THEN 1.0
      WHEN 'partial' THEN 0.5
      WHEN 'failure' THEN 0.0
      WHEN 'unknown' THEN NULL
      WHEN 'pending' THEN NULL
      ELSE NULL
    END AS outcome_score,
    fj.project
  FROM fok_judgments fj
  LEFT JOIN task_outcomes tout ON fj.outcome_id = tout.id
  -- Project scoping: when p_project is given, return only rows for THAT
  -- project. NULL-project rows are NOT bled into per-project results
  -- (matches memory_calibration_summary semantics).
  WHERE (p_project IS NULL OR fj.project = p_project)
),
filtered_joined AS (
  -- Calibration math requires both verdict_score AND outcome_score.
  -- 'unknown'/'skipped' verdicts (verdict_score IS NULL) and unlinked
  -- judgments (outcome_score IS NULL) are excluded from n and brier.
  SELECT verdict_score, outcome_score
  FROM judgments_with_scores
  WHERE verdict_score IS NOT NULL AND outcome_score IS NOT NULL
),
calibration_stats AS (
  SELECT
    COUNT(*)::integer AS total_count,
    AVG(POWER(verdict_score - outcome_score, 2)) AS brier_value
  FROM filtered_joined
)
SELECT
  cs.total_count,
  ROUND(cs.brier_value::numeric, 4),
  -- by_verdict counts judgments-with-an-outcome per verdict label.
  -- For named verdicts (sufficient/partial/insufficient), require both
  -- scores non-null (= contributes to calibration). For unknown/skipped,
  -- verdict_score is NULL by definition; require only outcome_score so
  -- we can surface "linked but uncalibratable" judgments instead of
  -- silently reporting 0.
  json_build_object(
    'sufficient', (SELECT COUNT(*) FROM judgments_with_scores WHERE verdict = 'sufficient' AND verdict_score IS NOT NULL AND outcome_score IS NOT NULL),
    'partial', (SELECT COUNT(*) FROM judgments_with_scores WHERE verdict = 'partial' AND verdict_score IS NOT NULL AND outcome_score IS NOT NULL),
    'insufficient', (SELECT COUNT(*) FROM judgments_with_scores WHERE verdict = 'insufficient' AND verdict_score IS NOT NULL AND outcome_score IS NOT NULL),
    'unknown', (SELECT COUNT(*) FROM judgments_with_scores WHERE verdict IN ('unknown', 'skipped') AND outcome_score IS NOT NULL)
  ),
  CASE
    WHEN cs.total_count < 30 THEN false
    ELSE (cs.brier_value >= 0.25)
  END AS drift_signal
FROM calibration_stats cs;
$$ ;
