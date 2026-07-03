"""Goal CRUD handlers — goal_set / goal_list / goal_get / goal_update.

Part of #360 server.py split. Calls utilities (`_get_client`,
`_audit_log`, embedding helpers, `EMBEDDING_MODEL_*`, ...) via
the `server` module so test monkeypatches on those names
propagate at call time.
"""

from __future__ import annotations

import json

from datetime import datetime, timezone  # noqa: F401

from mcp.types import TextContent  # noqa: F401

import server  # noqa: F401  — late-bound for monkeypatch propagation
import write_scrubber  # #999: Tier-2 write-path secret-scrubber gate

GOAL_FIELDS = (
    "slug",
    "title",
    "project",
    "direction",
    "priority",
    "status",
    "why",
    "success_criteria",
    "deadline",
    "progress",
    "progress_pct",
    "risks",
    "owner_focus",
    "jarvis_focus",
    "parent_id",
    "outcome",
    "lessons",
)


def _format_goal(g: dict) -> str:
    """Format a single goal for display."""
    deadline_str = f" | Deadline: {g['deadline']}" if g.get("deadline") else ""
    direction_str = f" | Direction: {g['direction']}" if g.get("direction") else ""
    parent_str = f" | Sub-goal of: {g['parent_id']}" if g.get("parent_id") else ""

    lines = [
        f"## {g['title']}",
        f"Slug: `{g['slug']}` | Project: {g.get('project') or 'cross-project'} | "
        f"Priority: {g['priority']} | Status: {g['status']}{deadline_str}{direction_str}{parent_str}",
    ]

    if g.get("why"):
        lines.append(f"\n**Why:** {g['why']}")

    if g.get("success_criteria"):
        criteria = g["success_criteria"]
        if isinstance(criteria, str):
            import json as _json

            try:
                criteria = _json.loads(criteria)
            except (ValueError, TypeError):
                criteria = [criteria]
        if criteria:
            lines.append("\n**Success criteria:**")
            for c in criteria:
                lines.append(f"- {c}")

    if g.get("progress"):
        progress = g["progress"]
        if isinstance(progress, str):
            import json as _json

            try:
                progress = _json.loads(progress)
            except (ValueError, TypeError):
                progress = []
        if progress:
            pct = g.get("progress_pct", 0)
            lines.append(f"\n**Progress ({pct}%):**")
            for p in progress:
                check = "x" if p.get("done") else " "
                lines.append(f"- [{check}] {p.get('item', p)}")

    if g.get("risks"):
        risks = g["risks"]
        if isinstance(risks, str):
            import json as _json

            try:
                risks = _json.loads(risks)
            except (ValueError, TypeError):
                risks = [risks]
        if risks:
            lines.append("\n**Risks:**")
            for r in risks:
                lines.append(f"- {r}")

    if g.get("owner_focus"):
        lines.append(f"\n**Owner focus:** {g['owner_focus']}")
    if g.get("jarvis_focus"):
        lines.append(f"**Jarvis focus:** {g['jarvis_focus']}")

    if g.get("outcome"):
        lines.append(f"\n**Outcome:** {g['outcome']}")
    if g.get("lessons"):
        lines.append(f"**Lessons:** {g['lessons']}")

    return "\n".join(lines)


async def _handle_goal_set(args: dict) -> list[TextContent]:
    client = server._get_client()
    slug = args["slug"]

    # #999: Tier-2 write-path scrubber backstop. Scan user-supplied free-text
    # fields before any DB write.
    block = write_scrubber.check_write(
        client,
        {
            k: args[k]
            for k in ("title", "why", "success_criteria", "risks", "owner_focus", "jarvis_focus", "outcome", "lessons")
            if k in args
        },
        write_path="goal_set",
    )
    if block is not None:
        return [TextContent(type="text", text=block)]

    data = {k: args[k] for k in GOAL_FIELDS if k in args}

    # Convert JSONB fields
    for field in ("success_criteria", "progress", "risks"):
        if field in data and isinstance(data[field], list):
            data[field] = json.dumps(data[field])

    # Upsert by slug
    existing = client.table("goals").select("id").eq("slug", slug).limit(1).execute()
    if existing.data:
        client.table("goals").update(data).eq("slug", slug).execute()
        server._audit_log(client, "goal_set", "update", slug)
        return [TextContent(type="text", text=f"Goal '{slug}' updated.")]
    else:
        client.table("goals").insert(data).execute()
        server._audit_log(client, "goal_set", "create", slug)
        return [TextContent(type="text", text=f"Goal '{slug}' created.")]


async def _handle_goal_list(args: dict) -> list[TextContent]:
    client = server._get_client()

    q = client.table("goals").select("*")

    status = args.get("status")
    project = args.get("project")
    priority = args.get("priority")

    if status:
        q = q.eq("status", status)
    if project:
        q = q.eq("project", project)
    if priority:
        q = q.eq("priority", priority)

    result = q.order("priority").order("deadline", desc=False, nullsfirst=False).execute()

    if not result.data:
        return [TextContent(type="text", text="No goals found.")]

    formatted = [_format_goal(g) for g in result.data]
    return [
        TextContent(
            type="text",
            text=f"# Goals ({len(result.data)})\n\n" + "\n\n---\n\n".join(formatted),
        )
    ]


async def _handle_goal_get(args: dict) -> list[TextContent]:
    client = server._get_client()
    slug = args["slug"]

    result = client.table("goals").select("*").eq("slug", slug).limit(1).execute()

    if not result.data:
        return [TextContent(type="text", text=f"Goal '{slug}' not found.")]

    return [TextContent(type="text", text=_format_goal(result.data[0]))]


async def _handle_goal_update(args: dict) -> list[TextContent]:
    client = server._get_client()
    slug = args["slug"]

    # #999: Tier-2 write-path scrubber backstop.
    block = write_scrubber.check_write(
        client,
        {
            k: args[k]
            for k in ("title", "why", "success_criteria", "risks", "owner_focus", "jarvis_focus", "outcome", "lessons")
            if k in args
        },
        write_path="goal_update",
    )
    if block is not None:
        return [TextContent(type="text", text=block)]

    data = {k: args[k] for k in GOAL_FIELDS if k in args and k != "slug"}

    if not data:
        return [TextContent(type="text", text="No fields to update.")]

    # Convert JSONB fields
    for field in ("success_criteria", "progress", "risks"):
        if field in data and isinstance(data[field], list):
            data[field] = json.dumps(data[field])

    # Auto-set closed_at when status changes to achieved/abandoned
    if data.get("status") in ("achieved", "abandoned"):
        data["closed_at"] = datetime.now(timezone.utc).isoformat()

    result = client.table("goals").update(data).eq("slug", slug).execute()

    if not result.data:
        return [TextContent(type="text", text=f"Goal '{slug}' not found.")]

    status_note = ""
    if data.get("status") in ("achieved", "abandoned"):
        status_note = f" Status: {data['status']}. closed_at set."

    updated_fields = [k for k in data if k != "closed_at"]
    server._audit_log(client, "goal_update", "update", slug, {"fields": updated_fields})
    return [TextContent(type="text", text=f"Goal '{slug}' updated.{status_note}")]


# -- Memory handlers --------------------------------------------------------
