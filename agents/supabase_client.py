"""Supabase bridge for LangGraph agents.

LangGraph agents call Supabase directly via ``supabase-py`` — MCP is
Claude Code's protocol and isn't available inside graph nodes. The
read/write helpers here mirror a subset of ``mcp-memory/server.py`` so
data written by an agent shows up in Claude Code's ``memory_recall`` /
``events_list`` / ``goal_list`` and vice versa.

Scope (Sprint 1, issue #173):
  * Reads — ``list_memories``, ``list_events``, ``list_goals``
  * Writes — ``store_event``, ``mark_event_processed``,
    ``update_goal_progress``, ``audit``

Anything more (memory_store, task_outcomes, consolidation, …) stays in
Claude Code for now. Agents are consumers of the event inbox, not full
owners of the knowledge base.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from agents.config import AgentConfig, load_config


def get_client(config: AgentConfig | None = None) -> Client:
    """Return a supabase-py client bound to the agent config.

    Raises ``RuntimeError`` with a pointer to ``.env.example`` if the
    caller hasn't set ``SUPABASE_URL`` / ``SUPABASE_KEY`` — a silent
    default would let bad config reach production unnoticed.
    """
    cfg = config or load_config()
    if not cfg.supabase_url or not cfg.supabase_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set for the agent Supabase "
            "bridge — see .env.example."
        )
    return create_client(cfg.supabase_url, cfg.supabase_key)


# -- Read helpers -----------------------------------------------------------


def list_memories(
    *,
    project: str | None = None,
    type: str | None = None,
    limit: int = 10,
    client: Client | None = None,
    config: AgentConfig | None = None,
) -> list[dict[str, Any]]:
    """Return memory rows — same table Claude Code reads via ``memory_recall``.

    Semantics mirror the MCP server (``mcp-memory/server.py``):

    * Soft-deleted rows (``deleted_at IS NOT NULL``) are excluded — agents
      must never see memories the user has removed.
    * When ``project`` is specified, global (NULL-project) memories are
      included alongside the project-scoped rows. Global memories carry
      cross-project rules/feedback that the agent still needs.

    Ordering matches the MCP server's default: most recently updated first.
    """
    cli = client or get_client(config)
    q = (
        cli.table("memories")
        .select("id, name, type, project, description, content, tags, updated_at")
        .is_("deleted_at", "null")
        .order("updated_at", desc=True)
        .limit(limit)
    )
    if project is not None:
        q = q.or_(f"project.eq.{project},project.is.null")
    if type is not None:
        q = q.eq("type", type)
    return q.execute().data or []


def list_events(
    *,
    processed: bool | None = False,
    repo: str | None = None,
    event_type: str | None = None,
    limit: int = 20,
    client: Client | None = None,
    config: AgentConfig | None = None,
) -> list[dict[str, Any]]:
    """Return event rows.

    ``processed=False`` (default) returns the unprocessed inbox, matching
    the MCP ``events_list`` default. Pass ``processed=None`` to include
    both.
    """
    cli = client or get_client(config)
    q = cli.table("events").select("*").order("created_at", desc=True).limit(limit)
    if processed is not None:
        q = q.eq("processed", processed)
        # FSM (#739): claim_next sets state='claimed' but leaves processed=false
        # until mark_processed runs. Filtering on the flag alone surfaces in-
        # flight rows; pin the "unprocessed inbox" to truly pending rows.
        if processed is False:
            q = q.eq("state", "pending")
    if repo is not None:
        q = q.eq("repo", repo)
    if event_type is not None:
        q = q.eq("event_type", event_type)
    return q.execute().data or []


def list_goals(
    *,
    status: str | None = "active",
    project: str | None = None,
    limit: int = 50,
    client: Client | None = None,
    config: AgentConfig | None = None,
) -> list[dict[str, Any]]:
    """Return goals — strategic context Claude Code loads at session start."""
    cli = client or get_client(config)
    # Ordering mirrors the MCP server's _handle_goal_list: priority first,
    # deadline as tiebreaker (NULL deadlines last — those are open-ended goals
    # and should fall behind anything with a concrete date).
    q = (
        cli.table("goals")
        .select(
            "slug, title, project, status, priority, why, "
            "success_criteria, progress_pct, updated_at"
        )
        .order("priority")
        .order("deadline", desc=False, nullsfirst=False)
        .limit(limit)
    )
    if status is not None:
        q = q.eq("status", status)
    if project is not None:
        q = q.eq("project", project)
    return q.execute().data or []


# -- Write helpers ----------------------------------------------------------


def store_event(
    *,
    event_type: str,
    repo: str,
    title: str,
    severity: str = "info",
    payload: dict[str, Any] | None = None,
    source: str = "langgraph-agent",
    dedup_key: str | None = None,
    client: Client | None = None,
    config: AgentConfig | None = None,
) -> dict[str, Any]:
    """Insert a row into the ``events`` inbox.

    Same queue Claude Code polls via ``events_list`` — agents can emit
    findings here and the orchestrator picks them up in its next loop.
    Returns the inserted row (includes the generated ``id``).

    ``dedup_key`` (#953 AC1/AC9) is written only when set — the column is
    unique-when-present, so emitting it on a re-observed terminal event lets
    the DB absorb the duplicate. When a ``dedup_key`` is supplied we ``upsert``
    on that column: a genuine duplicate resolves cleanly (idempotent
    re-observation) while real backend errors still propagate. The round-1 code
    used a plain ``insert`` and relied on a swallowed 409 to absorb the
    duplicate — which also masked legitimate insert failures (CRITICAL, #1011).
    Omitting ``dedup_key`` (``None``) keeps the row out of the unique index and
    uses a plain ``insert``, preserving behavior for non-dedup callers.
    """
    cli = client or get_client(config)
    row = {
        "event_type": event_type,
        "severity": severity,
        "repo": repo,
        "source": source,
        "title": title,
        "payload": payload or {},
    }
    if dedup_key is not None:
        row["dedup_key"] = dedup_key
        result = cli.table("events").upsert(row, on_conflict="dedup_key").execute()
        verb = "upserting"
    else:
        result = cli.table("events").insert(row).execute()
        verb = "inserting"
    data = result.data or []
    if not data:
        raise RuntimeError(f"Supabase returned no row after {verb} event: {row!r}")
    return data[0]


def mark_event_processed(
    event_id: str,
    *,
    processed_by: str,
    action_taken: str | None = None,
    client: Client | None = None,
    config: AgentConfig | None = None,
) -> None:
    """Close an event — mirrors the MCP ``events_mark_processed`` tool.

    Raises ``RuntimeError`` if ``event_id`` matched no row. A silent no-op
    would let the caller think the event was closed when nothing actually
    changed — typically a sign of a stale id or wrong-environment lookup.
    """
    cli = client or get_client(config)
    update: dict[str, Any] = {
        "processed": True,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "processed_by": processed_by,
    }
    if action_taken is not None:
        update["action_taken"] = action_taken
    result = cli.table("events").update(update).eq("id", event_id).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError(f"Event not found or not updated: event_id={event_id!r}")


def _parse_progress(raw: Any) -> list[Any]:
    """Normalize ``goals.progress`` into a list.

    PostgREST sometimes surfaces jsonb columns as already-decoded lists and
    sometimes as JSON strings (depends on client version + column config).
    The MCP ``_format_goal`` helper handles both; so must the agent bridge
    — otherwise a string payload would silently reset progress to ``[]``.
    Only a genuine parse failure (or a non-list/non-string value) falls
    back to an empty list.
    """
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return list(decoded) if isinstance(decoded, list) else []
    return []


def update_goal_progress(
    slug: str,
    progress_entry: dict[str, Any],
    *,
    client: Client | None = None,
    config: AgentConfig | None = None,
    max_retries: int = 3,
) -> None:
    """Append an entry to ``goals.progress`` (jsonb list).

    Matches the MCP ``goal_update`` semantics: progress is an append-only
    log of timestamped observations, not an overwrite.

    The update is read-modify-write, so concurrent writers could otherwise
    overwrite each other. We guard with optimistic concurrency: the UPDATE
    matches on the ``updated_at`` we just read, and on no-row-match we
    re-read and retry up to ``max_retries`` times before raising.
    """
    cli = client or get_client(config)
    for _ in range(max_retries):
        current = (
            cli.table("goals").select("progress, updated_at").eq("slug", slug).limit(1).execute()
        )
        rows = current.data or []
        if not rows:
            raise RuntimeError(f"Goal not found: slug={slug!r}")

        row = rows[0]
        prior = _parse_progress(row.get("progress"))
        next_progress = [*prior, progress_entry]
        next_updated_at = datetime.now(timezone.utc).isoformat()

        update_q = (
            cli.table("goals")
            .update({"progress": next_progress, "updated_at": next_updated_at})
            .eq("slug", slug)
        )
        current_updated_at = row.get("updated_at")
        if current_updated_at is None:
            update_q = update_q.is_("updated_at", "null")
        else:
            update_q = update_q.eq("updated_at", current_updated_at)

        result = update_q.execute()
        if result.data:
            return
        # No match → another writer committed between our read and write.
        # Re-read and retry with fresh state.

    raise RuntimeError(
        f"Concurrent update prevented appending goal progress after "
        f"{max_retries} retries: slug={slug!r}"
    )


def audit(
    *,
    agent_id: str,
    tool_name: str,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
    outcome: str = "success",
    client: Client | None = None,
    config: AgentConfig | None = None,
) -> None:
    """Best-effort audit entry — never raises.

    The ``agent_id`` column on ``audit_log`` is how agents identify
    themselves (e.g. ``"langgraph-monitor"``). Matches the server.py
    ``_audit_log()`` convention, with ``agent_id`` filled in — MCP writes
    leave it NULL, so the column doubles as the actor differentiator.
    """
    try:
        cli = client or get_client(config)
        cli.table("audit_log").insert(
            {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "action": action,
                "target": target,
                "details": details or {},
                "outcome": outcome,
            }
        ).execute()
    except Exception:
        # Audit is best-effort — never block operations on logging failure.
        pass
