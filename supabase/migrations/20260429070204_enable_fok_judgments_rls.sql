-- Pillar 4 / Memory FOK Phase 5.3-β (issue #443, follow-up to PR #470 review)
-- Enable RLS + allow-all policies on fok_judgments to match the Pillar
-- convention used by known_unknowns / task_queue. Without this, PostgREST
-- access semantics for the table differ from the rest of the schema.
-- Hardening to per-role policies is a separate sweep across the schema.

ALTER TABLE fok_judgments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for authenticated" ON fok_judgments
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for anon" ON fok_judgments
  FOR ALL TO anon USING (true) WITH CHECK (true);
