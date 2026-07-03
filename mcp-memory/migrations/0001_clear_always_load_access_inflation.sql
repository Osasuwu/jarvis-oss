-- Migration: Clear access-boost inflation for always_load memories
--
-- Issue: #767 - Access-boost de-bias for always_load auto-loads
-- Goal: Reset last_accessed_at timestamps for memories tagged with 'always_load'
--       to prevent recency-boost from dominating semantic recall scores via
--       ACT-R temporal scoring. Always-load rules are evergreen and should not
--       accumulate access recency.
--
-- Scope: Supabase "memories" table, rows with 'always_load' tag
-- Strategy: Set last_accessed_at to NULL (logical "never accessed") for
--           always_load memories. This resets their temporal score to baseline,
--           letting semantic + confidence scoring drive ranking instead.
--           Rationale: NULL != default timestamp; NULL allows normal temporal
--           scoring logic to work (scores access-boosted memories >= baseline).

UPDATE memories
SET last_accessed_at = NULL
WHERE 'always_load' = ANY(tags)
  AND last_accessed_at IS NOT NULL;

-- Post-migration verification query (run separately to audit):
-- SELECT name, tags, last_accessed_at, updated_at FROM memories
-- WHERE 'always_load' = ANY(tags)
-- ORDER BY updated_at DESC;
