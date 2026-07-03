"""Credential registry handlers — credential_list / credential_add /
credential_check_expiry (Pillar 9). Only metadata, never values.

Part of #360 server.py split. Calls utilities (`_get_client`,
`_audit_log`, embedding helpers, `EMBEDDING_MODEL_*`, ...) via
the `server` module so test monkeypatches on those names
propagate at call time.
"""

from __future__ import annotations

from datetime import datetime, timezone  # noqa: F401

from mcp.types import TextContent  # noqa: F401

import server  # noqa: F401  — late-bound for monkeypatch propagation


async def _handle_credential_list(args: dict) -> list[TextContent]:
    """List registered credentials — metadata only, never secret values."""
    client = server._get_client()
    query = (
        client.table("credential_registry")
        .select(
            "service, env_var, stored_in, scope, expires_at, last_rotated_at, rotation_notes, notes"
        )
        .order("service")
    )
    if args.get("scope"):
        query = query.eq("scope", args["scope"])

    result = query.execute()
    if not result.data:
        return [TextContent(type="text", text="No credentials registered.")]

    lines = [f"# Credential Registry ({len(result.data)} entries)\n"]
    for c in result.data:
        expiry = f" | Expires: {c['expires_at'][:10]}" if c.get("expires_at") else ""
        rotated = (
            f" | Last rotated: {c['last_rotated_at'][:10]}" if c.get("last_rotated_at") else ""
        )
        lines.append(f"**{c['service']}** — `{c['env_var']}`")
        lines.append(f"  Stored in: {c['stored_in']} | Scope: {c['scope']}{expiry}{rotated}")
        if c.get("rotation_notes"):
            lines.append(f"  Rotation: {c['rotation_notes']}")
        if c.get("notes"):
            lines.append(f"  Note: {c['notes']}")
        lines.append("")

    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_credential_add(args: dict) -> list[TextContent]:
    """Register a new credential (metadata only)."""
    client = server._get_client()

    row = {
        "service": args["service"],
        "env_var": args["env_var"],
        "stored_in": args.get("stored_in", ".env"),
        "scope": args.get("scope", "jarvis"),
    }
    for key in ("expires_at", "rotation_notes", "notes"):
        if args.get(key):
            row[key] = args[key]

    result = client.table("credential_registry").upsert(row, on_conflict="env_var").execute()
    if result.data:
        return [
            TextContent(
                type="text", text=f"Credential registered: {args['service']} ({args['env_var']})"
            )
        ]
    return [TextContent(type="text", text="Failed to register credential.")]


async def _handle_credential_check_expiry(args: dict) -> list[TextContent]:
    """Check for credentials expiring within N days."""
    client = server._get_client()
    days = args.get("days_ahead", 30)

    # Calculate the cutoff date
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    result = (
        client.table("credential_registry")
        .select("service, env_var, expires_at, rotation_notes")
        .not_.is_("expires_at", "null")
        .lte("expires_at", cutoff)
        .order("expires_at")
        .execute()
    )

    if not result.data:
        return [TextContent(type="text", text=f"No credentials expiring within {days} days.")]

    lines = [f"# Credentials expiring within {days} days ({len(result.data)})\n"]
    for c in result.data:
        exp_date = c["expires_at"][:10] if c.get("expires_at") else "?"
        lines.append(f"**{c['service']}** — `{c['env_var']}` — expires {exp_date}")
        if c.get("rotation_notes"):
            lines.append(f"  How to rotate: {c['rotation_notes']}")
        lines.append("")

    return [TextContent(type="text", text="\n".join(lines))]
