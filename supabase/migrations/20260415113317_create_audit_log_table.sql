-- Audit log: fire-and-forget trail of MCP tool invocations (tool, action,
-- target, outcome). Written by mcp-memory/client.py::_audit_log; never blocks
-- the caller.
--
-- Reconstructed retroactively 2026-07-08: applied to the remote ledger as
-- version 20260415113317 but the migration file was never committed (repo<->
-- ledger drift). Body mirrors the live deployed schema. RLS was added later;
-- see 20260708044124_enable_rls_credential_registry_audit_log.sql.
create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  "timestamp" timestamptz default now(),
  agent_id text,
  tool_name text not null,
  action text not null,
  target text,
  details jsonb default '{}'::jsonb,
  outcome text default 'success'
);

create index if not exists idx_audit_log_timestamp on audit_log ("timestamp" desc);
create index if not exists idx_audit_log_tool_name on audit_log (tool_name);
