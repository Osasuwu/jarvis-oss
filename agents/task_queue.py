"""Task queue interface — enqueue, claim_next, transition.

Built on the reshaped ``task_queue`` table (issue #740):
- FSM: ``pending → claimed → running → done | failed | parked | skipped_duplicate``
- Priority-ordered claiming (highest first, FIFO for ties)
- Idempotency-key dedup (colliding key on enqueue is a silent no-op)

Usage::

    from agents.task_queue import claim_next, enqueue, transition

    row = enqueue(goal="fix: tighten error path",
                  priority=10,
                  idempotency_key=sha256(...))
    if row is None:
        ...  # colliding key

    claimed = claim_next()
    if claimed is None:
        ...  # queue empty

    transition(claimed["id"], "running")
    transition(claimed["id"], "done")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

from agents.supabase_client import get_client

# -- FSM -------------------------------------------------------------------

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"claimed"}),
    "claimed": frozenset({"running"}),
    "running": frozenset({"done", "failed", "parked", "skipped_duplicate"}),
}

# `skipped_duplicate` (#931): dispatch-dedup found a live PR (or a live sibling
# row) for the task's issue after the running transition — terminal, no retry.
_TERMINAL_STATES: frozenset[str] = frozenset({"done", "failed", "parked", "skipped_duplicate"})


# -- Public API ------------------------------------------------------------


def enqueue(
    *,
    goal: str,
    priority: int = 0,
    assignee: str | None = None,
    idempotency_key: str,
    scope_files: list[str] | None = None,
    escalated_reason: str | None = None,
    client: Client | None = None,
) -> dict[str, Any] | None:
    """Insert a task into the queue.

    **Idempotent:** a colliding ``idempotency_key`` silently returns
    ``None`` instead of raising. Callers that need to detect collision
    should check the return value.

    ``escalated_reason`` is persisted on insert for ``assignee='owner'``
    escalation rows (the deterministic router's ``escalate_to_human``
    route, #744) so the owner sees *why* a task was escalated without a
    separate transition. The column already exists on ``task_queue``.

    Returns the inserted row, or ``None`` if the key already existed.
    """
    cli = client or get_client()
    row: dict[str, Any] = {
        "goal": goal,
        "priority": priority,
        "idempotency_key": idempotency_key,
        "status": "pending",
    }
    if assignee is not None:
        row["assignee"] = assignee
    if scope_files:
        row["scope_files"] = scope_files
    if escalated_reason is not None:
        row["escalated_reason"] = escalated_reason

    result = cli.table("task_queue").insert(row).execute()
    data = result.data or []
    return data[0] if data else None


def claim_next(
    *,
    assignee: str | None = None,
    client: Client | None = None,
) -> dict[str, Any] | None:
    """Claim the highest-priority pending task (priority desc, FIFO ties).

    When ``assignee`` is given, only pending rows with that assignee are
    eligible — the filter is applied in the SELECT, so a higher-priority
    row owned by a different assignee never shadows an eligible one. The
    task-dispatch loop (#909) passes ``assignee='sandcastle'`` so that
    ``assignee='owner'`` escalation rows are never auto-claimed/spawned.
    Omitting ``assignee`` preserves the original any-assignee behavior.

    Returns the claimed row with status updated to ``claimed``, or
    ``None`` if the queue is empty or another worker claimed the task
    first (optimistic lock).

    The returned dict includes the full row as it looked after the update,
    including ``id``, ``status``, ``claimed_at``, etc.
    """
    cli = client or get_client()

    # Read the highest-priority pending task
    query = cli.table("task_queue").select("*").eq("status", "pending")
    if assignee is not None:
        query = query.eq("assignee", assignee)
    rows = (
        query.order("priority", desc=True).order("created_at", desc=False).limit(1).execute()
    ).data or []

    if not rows:
        return None

    task = rows[0]

    # Optimistic-lock claim: only succeeds if row is still pending
    now = datetime.now(timezone.utc).isoformat()
    result = (
        cli.table("task_queue")
        .update({"status": "claimed", "claimed_at": now})
        .eq("id", task["id"])
        .eq("status", "pending")
        .execute()
    )

    updated = result.data or []
    return updated[0] if updated else None


def transition(
    task_id: str,
    to_status: str,
    *,
    reason: str | None = None,
    client: Client | None = None,
) -> dict[str, Any]:
    """Transition a task to a new status, validating the FSM.

    Parameters
    ----------
    task_id
        UUID of the task to transition.
    to_status
        Target FSM state. Must be a legal transition from the current
        state of the task.
    reason
        Optional reason attached to ``escalated_reason`` (useful for
        ``failed`` transitions to document the failure cause).

    Returns the updated row.

    Raises
    ------
    ValueError
        The transition is not allowed by the FSM (e.g. ``pending → done``
        without going through ``claimed → running``).
    RuntimeError
        The task does not exist, or another worker modified it between
        our read and write (optimistic lock failure).
    """
    cli = client or get_client()

    # Read current state
    rows = (
        cli.table("task_queue").select("status").eq("id", task_id).limit(1).execute()
    ).data or []

    if not rows:
        raise RuntimeError(f"Task not found: {task_id}")

    current_status = rows[0]["status"]

    if current_status in _TERMINAL_STATES:
        raise ValueError(f"Cannot transition from terminal state {current_status!r}")

    allowed = _VALID_TRANSITIONS.get(current_status, frozenset())
    if to_status not in allowed:
        raise ValueError(
            f"Illegal transition: {current_status!r} → {to_status!r}. "
            f"Allowed targets from {current_status!r}: "
            f"{sorted(allowed)}"
        )

    # Build update payload
    now = datetime.now(timezone.utc).isoformat()
    update: dict[str, Any] = {"status": to_status}

    if to_status == "claimed":
        update["claimed_at"] = now
    elif to_status in ("done", "failed", "skipped_duplicate"):
        update["completed_at"] = now

    if reason is not None:
        update["escalated_reason"] = reason

    # Optimistic lock: only succeeds if status hasn't changed since our read
    result = (
        cli.table("task_queue")
        .update(update)
        .eq("id", task_id)
        .eq("status", current_status)
        .execute()
    )

    updated = result.data or []
    if not updated:
        raise RuntimeError(
            f"Transition {current_status!r} → {to_status!r} failed: "
            f"task {task_id} was modified by another worker"
        )

    return updated[0]


def _cutoff_iso(older_than_seconds: float) -> str:
    """ISO-8601 timestamp ``older_than_seconds`` in the past (UTC).

    Used as the ``< claimed_at`` boundary for staleness queries. Computed
    client-side; the few-ms client/server clock skew is irrelevant against
    the 300s+ thresholds these helpers run with.
    """
    return (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()


def count_running(
    *,
    assignee: str,
    client: Client | None = None,
) -> int:
    """Count tasks currently in ``running`` for the given assignee.

    Backs the #909 concurrency cap: ``budget = cap − count_running(...)``,
    sampled once at drain start. Bounded by the cap (plus a handful of
    other-assignee rows), so a row count is cheap.
    """
    cli = client or get_client()
    rows = (
        cli.table("task_queue")
        .select("id")
        .eq("status", "running")
        .eq("assignee", assignee)
        .execute()
    ).data or []
    return len(rows)


def reclaim_stale_claimed(
    *,
    assignee: str,
    older_than_seconds: float,
    client: Client | None = None,
) -> int:
    """Return stale ``claimed`` rows to ``pending`` via a direct UPDATE.

    ``claimed`` strictly means *claimed but not yet spawned* under the #909
    Ordering-B contract (claim → transition(running) → spawn). A row stuck
    in ``claimed`` past ``older_than_seconds`` means the drainer died between
    the claim and the running transition; returning it to ``pending`` lets a
    later drain re-claim it. No process is running for it, so this is safe.

    This deliberately bypasses the FSM (``pending`` is not a legal target
    from ``claimed`` in :data:`_VALID_TRANSITIONS`) — it mirrors the events
    watchdog's ``reclaim_stale``. It never touches ``running`` rows (those
    have a live process; the reaper handles them). Returns the count
    reclaimed.
    """
    cli = client or get_client()
    result = (
        cli.table("task_queue")
        .update({"status": "pending", "claimed_at": None})
        .eq("status", "claimed")
        .eq("assignee", assignee)
        .lt("claimed_at", _cutoff_iso(older_than_seconds))
        .execute()
    )
    return len(result.data or [])


def requeue_running(
    task_id: str,
    *,
    client: Client | None = None,
) -> bool:
    """Return one ``running`` row to ``pending`` via a direct UPDATE (#921 AC4).

    For the mid-drain quota flip: under Ordering B the row is transitioned
    ``running`` *before* the spawn, so when the executor then declines to
    launch (throttled — no process exists), the row would otherwise sit
    ``running`` until the reaper fails it hours later. Requeueing it lets the
    next drain pick it up as soon as quota recovers.

    Like :func:`reclaim_stale_claimed`, this deliberately bypasses the FSM
    (``pending`` is not a legal target from ``running``). The ``status``
    filter is the optimistic lock: only a row still ``running`` is touched.
    Returns ``True`` iff the row was requeued. Callers tolerate ``False``
    (row changed under us) — the reaper remains the backstop.
    """
    cli = client or get_client()
    result = (
        cli.table("task_queue")
        .update({"status": "pending", "claimed_at": None})
        .eq("id", task_id)
        .eq("status", "running")
        .execute()
    )
    return bool(result.data)


def list_active(
    *,
    client: Client | None = None,
) -> list[dict[str, Any]]:
    """List live (``claimed`` or ``running``) rows for sibling dedup (#931).

    The dispatch-dedup check reads these to detect a *sibling* task_queue row
    already working the same issue. Only ``id``, ``goal`` and ``status`` are
    selected — the dedup predicate extracts the issue number from ``goal``.
    Two ``eq`` queries instead of ``in_`` keeps the call compatible with the
    minimal client stubs used across the test suite.
    """
    cli = client or get_client()
    rows: list[dict[str, Any]] = []
    for status in ("claimed", "running"):
        rows.extend(
            (
                cli.table("task_queue").select("id, goal, status").eq("status", status).execute()
            ).data
            or []
        )
    return rows


def list_stale_running(
    *,
    assignee: str,
    older_than_seconds: float,
    client: Client | None = None,
) -> list[dict[str, Any]]:
    """List ``running`` rows older than the reaper threshold for an assignee.

    Age is measured from ``claimed_at`` — there is no ``running_at`` column,
    and claimed→running is immediate under Ordering B, so ``claimed_at`` is
    a faithful proxy for running-start. The reaper is liveness-aware as of
    #921: the wake_driver filters out rows whose process it still tracks, so
    only *orphans* (rows with no tracked process — e.g. after a driver
    restart) are transitioned ``running → failed``. The threshold stays
    deliberately generous (≫ normal task runtime) as a backstop.
    """
    cli = client or get_client()
    # Only the id is needed — the reaper transitions by id; selecting "*" would
    # ship every column over the wire for no reader.
    rows = (
        cli.table("task_queue")
        .select("id")
        .eq("status", "running")
        .eq("assignee", assignee)
        .lt("claimed_at", _cutoff_iso(older_than_seconds))
        .execute()
    ).data or []
    return rows
