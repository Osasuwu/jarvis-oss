"""Supabase client factory + audit log (#360 split).

Lazy-init singleton pattern preserved from server.py. Audit log is
fire-and-forget — never raises into the caller.
"""

from __future__ import annotations

import os
import sys

_supabase = None


def _get_client():
    global _supabase
    if _supabase is not None:
        return _supabase

    url = os.environ.get("SUPABASE_URL")
    # Prefer service-role key on the host: after #542 RLS lands in live DB,
    # anon-key INSERTs are gated to source_provenance LIKE 'sandcastle:%'.
    # Host writes (skill:/hook:/session:/...) bypass RLS via service-role.
    # Sandcastle containers continue to ship only SUPABASE_KEY (anon) per
    # decision 228a2d9b — never set SUPABASE_SERVICE_KEY in .sandcastle/.env.
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        key = os.environ.get("SUPABASE_KEY")
        # Suppress in sandcastle containers — they intentionally ship anon-only
        # (decision 228a2d9b) and the warning would mis-instruct the operator.
        if key and not os.environ.get("SANDCASTLE_RUN_ID"):
            print(
                "WARNING: mcp-memory using SUPABASE_KEY (anon). After the #542 RLS "
                "migration is applied, host writes with non-sandcastle provenance "
                "(skill:/hook:/session:/...) will be rejected. Set SUPABASE_SERVICE_KEY "
                "in .env — see .env.example.",
                file=sys.stderr,
            )
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and one of SUPABASE_SERVICE_KEY / SUPABASE_KEY must be set. "
            "Get them from your Supabase project settings."
        )

    from supabase import create_client

    _supabase = create_client(url, key)

    # One-time migration: normalize legacy project='global' string rows to NULL.
    # Before the 2026-03-31 fix, 'global' was stored as a literal string instead
    # of NULL. This UPDATE is idempotent and safe to run on every startup.
    try:
        _supabase.table("memories").update({"project": None}).eq("project", "global").execute()
    except Exception:
        pass  # non-fatal — server still works without the migration

    return _supabase


# ---------------------------------------------------------------------------
# Audit logging — lightweight, fire-and-forget
# ---------------------------------------------------------------------------


def _audit_log(
    client, tool_name: str, action: str, target: str | None = None, details: dict | None = None
):
    """Fire-and-forget audit log entry. Never fails the caller."""
    try:
        client.table("audit_log").insert(
            {
                "tool_name": tool_name,
                "action": action,
                "target": target,
                "details": details or {},
            }
        ).execute()
    except Exception:
        pass  # audit is best-effort — never block operations
