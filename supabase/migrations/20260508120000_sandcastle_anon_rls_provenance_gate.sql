-- Slice 3 of sandcastle epic (#534, issue #542)
-- Decision: 228a2d9b-b57a-4d0f-8771-662482386b8a
--   Memory bridge: anon key + RLS, never service-role.
--
-- Tightens anon-key INSERT on the four tables sandcastle agents write to.
-- Service-role bypasses RLS automatically and is unaffected.
--
-- Per-table provenance column (schema asymmetry, see PR body):
--   memories         -> source_provenance LIKE 'sandcastle:%'
--   task_outcomes    -> source_provenance LIKE 'sandcastle:%'  (column added below)
--   episodes         -> actor LIKE 'sandcastle:%'              (actor IS the provenance field)
--   events_canonical -> actor LIKE 'sandcastle:%'              (actor IS the provenance field)
--
-- Anon SELECT/UPDATE/DELETE preserved as-is (current behaviour). Only INSERT is gated.
-- Paired with mcp-memory/schema.sql per #326 schema-drift CI gate.

-- =========================================================================
-- 1. Add source_provenance column to task_outcomes (nullable; existing rows
--    keep NULL). Matches the column already on memories (line 568 schema.sql).
-- =========================================================================
ALTER TABLE task_outcomes
  ADD COLUMN IF NOT EXISTS source_provenance text;

-- =========================================================================
-- 2. memories: split anon policies — INSERT gated by source_provenance prefix.
-- =========================================================================
DROP POLICY IF EXISTS "Allow all for anon" ON memories;

CREATE POLICY "Anon select" ON memories
  FOR SELECT TO anon USING (true);
CREATE POLICY "Anon update" ON memories
  FOR UPDATE TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Anon delete" ON memories
  FOR DELETE TO anon USING (true);
CREATE POLICY "Anon sandcastle insert" ON memories
  FOR INSERT TO anon
  WITH CHECK (source_provenance LIKE 'sandcastle:%');

-- =========================================================================
-- 3. task_outcomes: same shape.
-- =========================================================================
DROP POLICY IF EXISTS "Allow all for anon" ON task_outcomes;

CREATE POLICY "Anon select" ON task_outcomes
  FOR SELECT TO anon USING (true);
CREATE POLICY "Anon update" ON task_outcomes
  FOR UPDATE TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Anon delete" ON task_outcomes
  FOR DELETE TO anon USING (true);
CREATE POLICY "Anon sandcastle insert" ON task_outcomes
  FOR INSERT TO anon
  WITH CHECK (source_provenance LIKE 'sandcastle:%');

-- =========================================================================
-- 4. episodes: anon INSERT gated on `actor` (the column already used as the
--    provenance field — see schema.sql line 858-860 conventions).
-- =========================================================================
DROP POLICY IF EXISTS "Allow all for anon" ON episodes;

CREATE POLICY "Anon select" ON episodes
  FOR SELECT TO anon USING (true);
CREATE POLICY "Anon update" ON episodes
  FOR UPDATE TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Anon delete" ON episodes
  FOR DELETE TO anon USING (true);
CREATE POLICY "Anon sandcastle insert" ON episodes
  FOR INSERT TO anon
  WITH CHECK (actor LIKE 'sandcastle:%');

-- =========================================================================
-- 5. events_canonical: same — gated on `actor`.
-- =========================================================================
DROP POLICY IF EXISTS "Allow all for anon" ON events_canonical;

CREATE POLICY "Anon select" ON events_canonical
  FOR SELECT TO anon USING (true);
CREATE POLICY "Anon update" ON events_canonical
  FOR UPDATE TO anon USING (true) WITH CHECK (true);
CREATE POLICY "Anon delete" ON events_canonical
  FOR DELETE TO anon USING (true);
CREATE POLICY "Anon sandcastle insert" ON events_canonical
  FOR INSERT TO anon
  WITH CHECK (actor LIKE 'sandcastle:%');
