-- Credential registry (Pillar 9): a metadata inventory of credentials — service
-- name, env-var name, storage location, rotation/expiry — and NEVER the secret
-- values themselves (see mcp-memory/handlers/credential.py).
--
-- Reconstructed retroactively 2026-07-08: this table was applied to the remote
-- migration ledger as version 20260415082814, but the migration file was never
-- committed to the repo (repo<->ledger drift). Body mirrors the live deployed
-- schema exactly so a fresh `supabase db reset` reproduces production. RLS was
-- NOT part of the original create — it was added later; see
-- 20260708044124_enable_rls_credential_registry_audit_log.sql.
create table if not exists credential_registry (
  id uuid primary key default gen_random_uuid(),
  service text not null,
  env_var text not null unique,
  stored_in text not null default '.env',
  scope text not null default 'jarvis',
  created_at timestamptz default now(),
  expires_at timestamptz,
  last_rotated_at timestamptz,
  rotation_notes text,
  notes text,
  -- Defence-in-depth: reject rows whose metadata columns look like they carry a
  -- raw secret. Prefixes: JWT (eyJ), OpenAI (sk-), GitHub (ghp_/ghs_), AWS
  -- (AKIA), Slack (xox[bpras]-). env_var must never itself be a value.
  check (
    env_var !~ '^(eyJ|sk-|ghp_|ghs_|AKIA|xox[bpras]-)'
    and (rotation_notes is null or rotation_notes !~ '(eyJ|sk-|ghp_|ghs_|AKIA)')
    and (notes is null or notes !~ '(eyJ|sk-|ghp_|ghs_|AKIA)')
  )
);
