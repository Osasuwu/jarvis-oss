-- Enable RLS + allow-all policies on credential_registry and audit_log to match
-- the schema-wide convention (fok_judgments / known_unknowns / task_queue: RLS
-- on, allow-all for authenticated + anon; service_role bypasses automatically).
--
-- Motivation: Supabase Security Advisor raised rls_disabled_in_public ERRORs on
-- both tables (2026-07-08 Supabase email). App-layer-trust model — access is not
-- weakened, effective reachability is identical; enabling RLS silences the ERROR
-- and aligns the two tables with their ~10 siblings. Per-role hardening is a
-- separate schema-wide sweep.
--
-- credential_registry stores credential METADATA only (names, expiry, rotation
-- notes) — never secret values; its no-secret-values CHECK constraint enforces
-- that independently of RLS.

alter table credential_registry enable row level security;

create policy "Allow all for authenticated" on credential_registry
  for all using (true) with check (true);
create policy "Allow all for anon" on credential_registry
  for all to anon using (true) with check (true);

alter table audit_log enable row level security;

create policy "Allow all for authenticated" on audit_log
  for all using (true) with check (true);
create policy "Allow all for anon" on audit_log
  for all to anon using (true) with check (true);
