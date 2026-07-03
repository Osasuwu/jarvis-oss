-- Slice 3.5 of sandcastle epic (#534, issue #565)
-- Decision: f3b85eeb-b883-4891-b349-f64c8c9dc28c (extends 228a2d9b-b57a-4d0f-8771-662482386b8a)
--
-- Slice 3 (migration 20260508120000) gated anon INSERT with
--   provenance LIKE 'sandcastle:%' on memories / task_outcomes / episodes /
--   events_canonical, but UPDATE + DELETE remained wide-open for anon.
-- That left two holes:
--   (a) anon DELETE could wipe arbitrary rows (host data evaporates)
--   (b) anon UPDATE could rewrite source_provenance / actor — forging or
--       erasing audit, which defeats the slice 3 INSERT gate
-- This slice replaces the unconditional anon UPDATE/DELETE policies with
-- ones gated by the same provenance predicate as INSERT. Service-role
-- bypasses RLS and is unaffected.
--
-- Sequencing: requires #564 (host MCP on SUPABASE_SERVICE_KEY) — already
-- live as of 20260508120000 + host rotation. Otherwise host's own UPDATE
-- on legacy non-sandcastle rows would also break.
--
-- Anon SELECT preserved as-is (slice 3 left it open; this slice does not
-- touch read access).
-- Paired with mcp-memory/schema.sql per #326 schema-drift CI gate.

-- =========================================================================
-- 1. memories: replace open UPDATE/DELETE with sandcastle-gated.
-- =========================================================================
DROP POLICY IF EXISTS "Anon update" ON memories;
DROP POLICY IF EXISTS "Anon delete" ON memories;

CREATE POLICY "Anon sandcastle update" ON memories
  FOR UPDATE TO anon
  USING (source_provenance LIKE 'sandcastle:%')
  WITH CHECK (source_provenance LIKE 'sandcastle:%');

CREATE POLICY "Anon sandcastle delete" ON memories
  FOR DELETE TO anon
  USING (source_provenance LIKE 'sandcastle:%');

-- =========================================================================
-- 2. task_outcomes: same shape.
-- =========================================================================
DROP POLICY IF EXISTS "Anon update" ON task_outcomes;
DROP POLICY IF EXISTS "Anon delete" ON task_outcomes;

CREATE POLICY "Anon sandcastle update" ON task_outcomes
  FOR UPDATE TO anon
  USING (source_provenance LIKE 'sandcastle:%')
  WITH CHECK (source_provenance LIKE 'sandcastle:%');

CREATE POLICY "Anon sandcastle delete" ON task_outcomes
  FOR DELETE TO anon
  USING (source_provenance LIKE 'sandcastle:%');

-- =========================================================================
-- 3. episodes: gated on `actor` (the column already used as provenance).
-- =========================================================================
DROP POLICY IF EXISTS "Anon update" ON episodes;
DROP POLICY IF EXISTS "Anon delete" ON episodes;

CREATE POLICY "Anon sandcastle update" ON episodes
  FOR UPDATE TO anon
  USING (actor LIKE 'sandcastle:%')
  WITH CHECK (actor LIKE 'sandcastle:%');

CREATE POLICY "Anon sandcastle delete" ON episodes
  FOR DELETE TO anon
  USING (actor LIKE 'sandcastle:%');

-- =========================================================================
-- 4. events_canonical: same — gated on `actor`.
-- =========================================================================
DROP POLICY IF EXISTS "Anon update" ON events_canonical;
DROP POLICY IF EXISTS "Anon delete" ON events_canonical;

CREATE POLICY "Anon sandcastle update" ON events_canonical
  FOR UPDATE TO anon
  USING (actor LIKE 'sandcastle:%')
  WITH CHECK (actor LIKE 'sandcastle:%');

CREATE POLICY "Anon sandcastle delete" ON events_canonical
  FOR DELETE TO anon
  USING (actor LIKE 'sandcastle:%');
