"""Escalation triggers for the task dispatcher (issue #299, S2-4).

Dispatcher (S2-3, #298) calls the check functions here before dispatching
a ``task_queue`` row. If any trigger fires, :func:`escalate` moves the
row to ``escalated`` state and writes an `events` row with
``severity=HIGH`` so the owner sees it in the normal event-monitor path
(Sprint 1) or via `/status`.

Five triggers — split into pure checks (no DB writes, trivially testable)
and one DB-writing :func:`escalate` helper. Dispatcher wires them
together.

1. **Stale approval** — approval sat too long; re-approve before running.
2. **Scope drift** — files in ``scope_files`` changed after approval;
   the approval isn't valid for the current state.
3. **Limit near-exhaustion** — :mod:`agents.usage_probe` says budget is
   low; pause rather than burn the last tokens on auto-dispatch.
4. **Cross-task conflict** — another ``dispatched`` row touches overlapping
   ``scope_files``; avoid interleaved edits.
5. **Pattern repeat** — >3 successful dispatches of the same ``goal`` in
   a row; guards against a runaway loop.

Thresholds are module-level constants so tuning is a one-line change.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from agents import supabase_client
from agents.usage_probe import UsageReading

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable thresholds. Change here and the rule changes everywhere.
# ---------------------------------------------------------------------------

STALE_APPROVAL_MAX_DAYS = 7
PATTERN_REPEAT_THRESHOLD = 3

DISPATCHER_AGENT_ID = "task-dispatcher"
ESCALATION_EVENT_TYPE = "dispatcher_escalation"
ESCALATION_SEVERITY = "high"


class Trigger(str, Enum):
    """Reason an escalation fired. Becomes the ``trigger`` payload field."""

    STALE_APPROVAL = "stale_approval"
    SCOPE_DRIFT = "scope_drift"
    LIMIT_NEAR_EXHAUSTION = "limit_near_exhaustion"
    CROSS_TASK_CONFLICT = "cross_task_conflict"
    PATTERN_REPEAT = "pattern_repeat"


@dataclass(frozen=True)
class EscalationCheck:
    """Outcome of a single trigger check.

    ``context`` is free-form dict captured for the event payload so the
    owner can reason about *why* without re-running the check. Keep it
    small and JSON-safe — it round-trips through Supabase jsonb.
    """

    should_escalate: bool
    trigger: Trigger | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def no_action(cls) -> "EscalationCheck":
        return cls(should_escalate=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_timestamptz(value: Any) -> datetime | None:
    """Parse Supabase's timestamptz value (str or datetime) — ``None`` on fail.

    Supabase-py sometimes returns ISO strings, sometimes parsed datetimes,
    depending on client version and column config. Handle both; unknown
    shapes return None so callers decide how to degrade (we treat "can't
    parse" as "don't escalate — no evidence").
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            # fromisoformat handles "2026-04-22T12:34:56+00:00" and "...Z" in 3.11+.
            normalized = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Pure checks — no DB writes, safe to run against synthetic rows in tests.
# ---------------------------------------------------------------------------


def check_stale_approval(
    row: dict[str, Any],
    *,
    max_age_days: int = STALE_APPROVAL_MAX_DAYS,
    now: datetime | None = None,
) -> EscalationCheck:
    """Escalate if ``now - approved_at > max_age_days``."""
    approved_at = _parse_timestamptz(row.get("approved_at"))
    if approved_at is None:
        return EscalationCheck.no_action()
    current = now or _now_utc()
    age = current - approved_at
    if age > timedelta(days=max_age_days):
        return EscalationCheck(
            should_escalate=True,
            trigger=Trigger.STALE_APPROVAL,
            context={
                "approved_at": approved_at.isoformat(),
                "age_days": age.days,
                "max_age_days": max_age_days,
            },
        )
    return EscalationCheck.no_action()


def check_scope_drift(
    row: dict[str, Any],
    *,
    current_scope_hash: str | Callable[[Iterable[str]], str],
) -> EscalationCheck:
    """Escalate if ``current_scope_hash != row['approved_scope_hash']``.

    ``current_scope_hash`` can be a precomputed string or a callable that
    takes ``scope_files`` and returns the hash (the hashing function lives
    in S2-3 dispatcher; this module stays transport-agnostic).
    """
    approved_hash = row.get("approved_scope_hash")
    if not approved_hash:
        # No baseline to drift from — a row without an approved hash
        # shouldn't have reached dispatch anyway. Caller's bug, not ours.
        return EscalationCheck.no_action()
    if callable(current_scope_hash):
        scope_files = row.get("scope_files") or []
        try:
            current = current_scope_hash(scope_files)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[escalation] scope hash function raised -- treating as drift: %s", exc)
            return EscalationCheck(
                should_escalate=True,
                trigger=Trigger.SCOPE_DRIFT,
                context={"error": f"{type(exc).__name__}: {exc}"},
            )
    else:
        current = current_scope_hash
    if current != approved_hash:
        return EscalationCheck(
            should_escalate=True,
            trigger=Trigger.SCOPE_DRIFT,
            context={"approved_scope_hash": approved_hash, "current_scope_hash": current},
        )
    return EscalationCheck.no_action()


def check_limit_near_exhaustion(reading: UsageReading) -> EscalationCheck:
    """Escalate when the usage probe reports budget near-exhaustion."""
    if reading.near_exhaustion:
        return EscalationCheck(
            should_escalate=True,
            trigger=Trigger.LIMIT_NEAR_EXHAUSTION,
            context={
                "used": reading.used,
                "total": reading.total,
                "headroom_ratio": reading.headroom_ratio,
            },
        )
    return EscalationCheck.no_action()


def check_cross_task_conflict(
    row: dict[str, Any],
    *,
    active_dispatched_rows: Iterable[dict[str, Any]],
) -> EscalationCheck:
    """Escalate if another dispatched row's ``scope_files`` overlaps this one.

    ``active_dispatched_rows`` comes from the dispatcher's scan — the rows
    with ``status='dispatched'`` that aren't this one. Dispatcher's
    responsibility to exclude the current row; we don't check ids here.
    """
    own_files = set(row.get("scope_files") or [])
    if not own_files:
        return EscalationCheck.no_action()
    own_id = row.get("id")
    for other in active_dispatched_rows:
        if other.get("id") == own_id:
            # Defensive: dispatcher should've excluded us, but a safety net
            # costs one comparison and prevents a row flagging itself.
            continue
        other_files = set(other.get("scope_files") or [])
        overlap = own_files & other_files
        if overlap:
            return EscalationCheck(
                should_escalate=True,
                trigger=Trigger.CROSS_TASK_CONFLICT,
                context={
                    "conflicting_task_id": str(other.get("id")),
                    "overlapping_files": sorted(overlap),
                },
            )
    return EscalationCheck.no_action()


def check_pattern_repeat(
    row: dict[str, Any],
    *,
    recent_successful_dispatches: Iterable[dict[str, Any]],
    threshold: int = PATTERN_REPEAT_THRESHOLD,
) -> EscalationCheck:
    """Escalate when the same ``goal`` appears > ``threshold`` times consecutively.

    ``recent_successful_dispatches`` should be ordered newest-first
    (standard Supabase ``order('dispatched_at', desc=True)``). We count the
    run of matching goals from the most recent entry; a different goal
    anywhere in the run resets the count.
    """
    goal = row.get("goal")
    if not goal:
        return EscalationCheck.no_action()
    run_length = 0
    for past in recent_successful_dispatches:
        if past.get("goal") == goal:
            run_length += 1
        else:
            break
    if run_length > threshold:
        return EscalationCheck(
            should_escalate=True,
            trigger=Trigger.PATTERN_REPEAT,
            context={
                "goal": goal,
                "recent_matching_dispatches": run_length,
                "threshold": threshold,
            },
        )
    return EscalationCheck.no_action()


# ---------------------------------------------------------------------------
# Aggregator — dispatcher's single entry point.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EscalationContext:
    """Bundle of context every check needs — keeps dispatcher's call site terse.

    ``current_scope_hash`` mirrors :func:`check_scope_drift` — precomputed
    string or callable. ``usage_reading`` is the cached probe reading for
    this tick.
    """

    current_scope_hash: str | Callable[[Iterable[str]], str]
    usage_reading: UsageReading
    active_dispatched_rows: Iterable[dict[str, Any]] = field(default_factory=tuple)
    recent_successful_dispatches: Iterable[dict[str, Any]] = field(default_factory=tuple)


def check_all(row: dict[str, Any], ctx: EscalationContext) -> EscalationCheck:
    """Run every trigger; return the first that fires, or ``no_action``.

    Order: stale approval → scope drift → limit near-exhaustion →
    cross-task conflict → pattern repeat. First-match is intentional —
    the reason surfaced to the owner should be the one the owner can act
    on first (re-approve, fix the conflict, wait for budget).
    """
    for check in (
        lambda: check_stale_approval(row),
        lambda: check_scope_drift(row, current_scope_hash=ctx.current_scope_hash),
        lambda: check_limit_near_exhaustion(ctx.usage_reading),
        lambda: check_cross_task_conflict(row, active_dispatched_rows=ctx.active_dispatched_rows),
        lambda: check_pattern_repeat(
            row, recent_successful_dispatches=ctx.recent_successful_dispatches
        ),
    ):
        result = check()
        if result.should_escalate:
            return result
    return EscalationCheck.no_action()


# ---------------------------------------------------------------------------
# DB side-effect: write event + flip row status.
# ---------------------------------------------------------------------------


def escalate(
    row: dict[str, Any],
    check: EscalationCheck,
    *,
    client: Any | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Persist an escalation: write `events` row + flip `task_queue.status`.

    Returns the inserted event row. Raises if the check didn't actually
    flag an escalation — easier to catch misuse than to silently skip.
    """
    if not check.should_escalate or check.trigger is None:
        raise ValueError("escalate() called on a non-escalating check")

    cli = client or supabase_client.get_client(config)
    queue_id = row.get("id")
    reason = f"{check.trigger.value}: {check.context}"

    # Event first — dispatcher writes this in tier-0 territory (events is
    # on the Sprint-1 allowlist). Queue update is tier-0 too (status flip
    # within approved rows).
    event_payload = {
        "queue_id": str(queue_id) if queue_id is not None else None,
        "trigger": check.trigger.value,
        "context": check.context,
    }
    event_row = (
        cli.table("events")
        .insert(
            {
                "event_type": ESCALATION_EVENT_TYPE,
                "severity": ESCALATION_SEVERITY,
                "repo": os.environ.get("GITHUB_REPO", "your-username/your-repo"),
                "source": DISPATCHER_AGENT_ID,
                "title": f"Dispatcher escalated task {queue_id}: {check.trigger.value}",
                "payload": event_payload,
            }
        )
        .execute()
    )
    event_data = (event_row.data or [{}])[0]

    # Queue status update — only if we know the id. An unidentifiable row
    # means the caller constructed it by hand; the event still carries the
    # signal, so don't fail the whole escalation on a missing id.
    if queue_id is not None:
        (
            cli.table("task_queue")
            .update({"status": "escalated", "escalated_reason": reason})
            .eq("id", queue_id)
            .execute()
        )

    return event_data
