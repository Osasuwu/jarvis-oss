-- Fix Supabase Security Advisor security_definer_view ERROR on memory_calibration
-- (2026-07-08 Supabase email). A view owned by postgres runs with definer rights
-- by default, bypassing the querying role's RLS. security_invoker = on makes the
-- view enforce the caller's RLS instead — the correct posture for a view exposed
-- through PostgREST. Paired with mcp-memory/schema.sql per #326 schema-drift gate.

alter view public.memory_calibration set (security_invoker = on);
