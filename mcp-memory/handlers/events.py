"""Event tap handlers — events_list, events_mark_processed, and event queue FSM.

Surfaces row reads and FSM operations from the events table that drive
perception in Pillar 7.

Part of #360 server.py split. Calls utilities (`_get_client`,
`_audit_log`, embedding helpers, `EMBEDDING_MODEL_*`, ...) via
the `server` module so test monkeypatches on those names
propagate at call time.

The `state` FSM column (pending|claimed|processed|parked) and its RPCs
(`claim_next`, `mark_processed`, `park_event`, `requeue_event`) have been
the authoritative event lifecycle tracker since #739. The legacy
`processed` boolean and its compat fallback were decommissioned in #1391.
"""

from __future__ import annotations

import json

from datetime import datetime, timezone  # noqa: F401

from mcp.types import TextContent  # noqa: F401

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _get_client():
    """Lazy import to avoid circular import (server imports this module)."""
    import server  # noqa: F401
    return server._get_client()


def _is_missing_rpc(err: Exception) -> bool:
    """PostgREST/PG signature for an undefined RPC function (42883)."""
    msg = str(err).lower()
    return "42883" in msg or "could not find the function" in msg or (
        "does not exist" in msg and "function" in msg
    )


def _missing_fsm_error_text(rpc_name: str) -> str:
    return (
        f"Event queue FSM RPC `{rpc_name}` not available on this database. "
        "Apply migration supabase/migrations/20260521130515_extend_events_queue.sql, "
        "then retry."
    )


async def _handle_events_list(args: dict) -> list[TextContent]:
    client = _get_client()

    q = client.table("events").select("*")

    if not args.get("include_processed", False):
        q = q.or_("state.eq.pending,state.eq.claimed,state.eq.parked")

    if args.get("repo"):
        q = q.eq("repo", args["repo"])
    if args.get("event_type"):
        q = q.eq("event_type", args["event_type"])

    min_severity = args.get("severity")
    if min_severity and min_severity in SEVERITY_ORDER:
        allowed = [s for s, v in SEVERITY_ORDER.items() if v <= SEVERITY_ORDER[min_severity]]
        q = q.in_("severity", allowed)

    limit = args.get("limit", 20)
    result = q.order("created_at", desc=True).limit(limit).execute()

    if not result.data:
        return [TextContent(type="text", text="No events found.")]

    # Sort by severity (critical first), then by time
    events = sorted(
        result.data, key=lambda e: (SEVERITY_ORDER.get(e["severity"], 4), e["created_at"])
    )

    lines = [f"# Events ({len(events)})\n"]
    for ev in events:
        state_mark = f" [{ev.get('state', 'unknown').upper()}]" if ev.get("state") else ""
        payload_str = ""
        if ev.get("payload"):
            p = ev["payload"]
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except (ValueError, TypeError):
                    p = {}
            if p.get("url"):
                payload_str = f"\n  URL: {p['url']}"

        lines.append(
            f"## [{ev['severity'].upper()}] {ev['title']}{state_mark}\n"
            f"  ID: `{ev['id']}` | State: {ev.get('state', 'N/A')}\n"
            f"  Type: {ev['event_type']} | Repo: {ev['repo']} | Source: {ev['source']}\n"
            f"  Time: {ev.get('event_at', ev['created_at'])}"
            f"{payload_str}\n"
        )

    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_events_mark_processed(args: dict) -> list[TextContent]:
    client = _get_client()

    event_ids = args["event_ids"]
    processed_by = args["processed_by"]
    action_taken = args.get("action_taken", "")

    try:
        updated = sum(
            1
            for eid in event_ids
            if client.rpc(
                "mark_processed",
                {"event_id": eid, "processor": processed_by, "action_taken": action_taken},
            ).execute().data
        )
    except Exception as e:
        if _is_missing_rpc(e):
            return [TextContent(type="text", text=_missing_fsm_error_text("mark_processed"))]
        raise

    return [
        TextContent(type="text", text=f"Marked {updated}/{len(event_ids)} events as processed.")
    ]


# ---------------------------------------------------------------------------
# Event queue FSM tools (#739)
# ---------------------------------------------------------------------------


async def _handle_event_claim_next(args: dict) -> list[TextContent]:
    """Claim the highest-priority pending event for processing."""
    client = _get_client()
    claimer = args["claimer"]

    try:
        result = client.rpc("claim_next", {"claimer": claimer}).execute()
    except Exception as e:
        if _is_missing_rpc(e):
            return [TextContent(type="text", text=_missing_fsm_error_text("claim_next"))]
        raise

    if not result.data:
        return [TextContent(type="text", text="No pending events to claim.")]

    event = result.data[0]
    return [
        TextContent(
            type="text",
            text=(
                f"Claimed event `{event['id']}`\n"
                f"  Type: {event.get('event_type', 'N/A')}\n"
                f"  Severity: {event.get('severity', 'N/A')}\n"
                f"  Title: {event.get('title', 'N/A')}\n"
                f"  Repo: {event.get('repo', 'N/A')}\n"
                f"  Claimed by: {claimer}"
            ),
        )
    ]


async def _handle_event_mark_processed(args: dict) -> list[TextContent]:
    """Transition a claimed event to processed via the FSM."""
    client = _get_client()
    event_id = args["event_id"]
    processor = args["processor"]
    action_taken = args.get("action_taken", "")

    try:
        result = client.rpc(
            "mark_processed",
            {"event_id": event_id, "processor": processor, "action_taken": action_taken},
        ).execute()
    except Exception as e:
        if _is_missing_rpc(e):
            return [TextContent(type="text", text=_missing_fsm_error_text("mark_processed"))]
        raise

    if result.data:
        return [
            TextContent(
                type="text",
                text=f"Event `{event_id}` marked as processed by {processor}.",
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"Could not mark event `{event_id}` as processed — not in 'claimed' state or not found.",
        )
    ]


async def _handle_event_park(args: dict) -> list[TextContent]:
    """Park a claimed event (blocked on dependency)."""
    client = _get_client()
    event_id = args["event_id"]
    reason = args.get("reason", "")

    try:
        result = client.rpc("park_event", {"event_id": event_id, "reason": reason}).execute()
    except Exception as e:
        if _is_missing_rpc(e):
            return [TextContent(type="text", text=_missing_fsm_error_text("park_event"))]
        raise

    if result.data:
        return [
            TextContent(
                type="text",
                text=f"Event `{event_id}` parked. Reason: {reason or 'unspecified'}.",
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"Could not park event `{event_id}` — not in 'claimed' state or not found.",
        )
    ]


async def _handle_event_requeue(args: dict) -> list[TextContent]:
    """Re-queue a parked or claimed event back to pending."""
    client = _get_client()
    event_id = args["event_id"]
    reason = args.get("reason", "")

    try:
        result = client.rpc("requeue_event", {"event_id": event_id, "reason": reason}).execute()
    except Exception as e:
        if _is_missing_rpc(e):
            return [TextContent(type="text", text=_missing_fsm_error_text("requeue_event"))]
        raise

    if result.data:
        return [
            TextContent(
                type="text",
                text=f"Event `{event_id}` requeued to pending. Reason: {reason or 'unspecified'}.",
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"Could not requeue event `{event_id}` — not in 'claimed' or 'parked' state or not found.",
        )
    ]
