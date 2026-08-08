"""Parked-event re-queue poller — Path B (#745).

When the orchestrator parks an event because it is blocked on a sandcastle
task, the poller periodically checks whether the blocking task has reached
a terminal state and re-queues the event accordingly.

- Task ``done`` → event is re-queued to ``pending``. The wake_driver runs the
  poll step (Step 2b) *before* the event drain (Step 3) in the same tick, so
  the re-queued event is drained within that same tick, not the next one.
- Task ``failed`` → event is re-queued to ``pending`` (not silently dropped;
  the orchestrator will re-route it per the deterministic routing table).
- Task ``parked`` → event is re-queued to ``pending`` as well. ``parked`` is a
  *terminal* task state (``task_queue._TERMINAL_STATES``) — the blocking task
  will never advance to ``done``, so leaving the event parked strands it
  forever. Requeueing hands it back to the orchestrator to re-route, with a
  distinct reason so the cause is recoverable.
- Task still ``running`` (or no such task) → event stays ``parked``.
- No ``blocked_by_task_id`` in event payload → skipped (parked for a
  different reason).

Usage::

    from agents import poller

    requeued = poller.poll(port)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# -- Port ---------------------------------------------------------------------


class PollerPort(Protocol):
    """Interface the poller depends on for events and task_queue access.

    Implemented by an in-memory fake in tests, and by
    ``wake_driver.PsycopgEventQueue`` in production (#1393) — ``main()``
    passes ``poller_port=queue`` unconditionally at both the ``--once`` and
    long-running call sites. The consumer side of Path B is live; nothing in
    production yet *writes* ``blocked_by_task_id`` into a parked event's
    payload, so the poller runs every tick and finds nothing until a producer
    exists — tracked in #1455.
    """

    def find_parked_events(self) -> list[dict[str, Any]]:
        """Return every ``parked`` event whose payload contains ``blocked_by_task_id``.

        Returns a list of event dicts. Each dict must have at least ``id``
        and ``payload`` keys. Events parked for other reasons (no
        ``blocked_by_task_id``) are filtered out by the port implementation.
        """

    def get_task_statuses(self, task_ids: list[str]) -> dict[str, str]:
        """Look up several tasks' current FSM status in one round-trip.

        A task id with no matching row is absent from the returned dict —
        same "absent means unknown" contract as a single-row lookup.
        Expected status values: ``"pending"``, ``"claimed"``, ``"running"``,
        ``"done"``, ``"failed"``, ``"parked"``. The poller only acts on
        terminal states (``done`` / ``failed`` / ``parked``); all others
        leave the event parked for the next pass.

        Batched (#1475 review, MEDIUM — the M2 finding PR #964 deferred
        until the production adapter shipped) so one poll sweep costs one
        task_queue round-trip instead of one per parked event.
        """

    def requeue_event(self, event_id: str, *, reason: str) -> bool:
        """Transition a parked event back to ``pending``.

        Returns ``True`` if the event was successfully requeued.
        """


# -- Core logic ---------------------------------------------------------------


# Task FSM states that release a parked event back to ``pending``, mapped to the
# clause used in the requeue reason. All three are terminal in
# ``task_queue._TERMINAL_STATES``: a blocking task in any of them will never
# advance, so the waiting event must be re-routed by the orchestrator rather
# than stranded. ``parked`` is the #964 fix — it is terminal too, so an event
# blocked on a parked task was previously stranded forever.
_REQUEUE_REASONS: dict[str, str] = {
    "done": "completed",
    "failed": "failed",
    "parked": "parked (terminal) — orchestrator re-routes",
    # #931: a task skipped as a duplicate is terminal — an event blocked on it
    # will never see it advance, so re-route rather than strand it. The live PR /
    # sibling row that triggered the skip is the work the event should follow.
    "skipped_duplicate": "skipped as duplicate (terminal) — orchestrator re-routes",
}

# Import-time guard: if task_queue adds a new terminal state the poller must
# decide how to requeue it — fail fast rather than silently stranding events.
from agents.task_queue import _TERMINAL_STATES as _TASK_TERMINAL_STATES  # noqa: E402

assert set(_REQUEUE_REASONS.keys()) == _TASK_TERMINAL_STATES, (
    f"_REQUEUE_REASONS keys {set(_REQUEUE_REASONS.keys())} != "
    f"task_queue._TERMINAL_STATES {_TASK_TERMINAL_STATES}"
)


def poll(port: PollerPort) -> int:
    """Check all parked events with a ``blocked_by_task_id`` reference.

    For each parked event:
    - If the blocking task reached a terminal state (``done`` / ``failed`` /
      ``parked``) → requeue the event. The orchestrator re-routes it — it is
      never silently dropped.
    - If the blocking task is still ``running``, or the task no longer
      exists → leave the event parked.

    Returns the number of events requeued.

    Statuses are fetched in one batch call before the per-event loop (#1475
    review, MEDIUM — the M2 finding PR #964 deferred until the production
    adapter shipped): one round-trip per sweep instead of one per parked
    event. Trade-off: failure granularity moves from per-event to per-pass —
    if the batch call itself raises, every parked event this pass is left
    parked (not lost) for the next sweep, rather than isolating just the one
    bad task id the way a per-event call would.
    """
    parked = port.find_parked_events()
    if not parked:
        return 0

    task_ids = [tid for tid in (_blocking_task_id(event) for event in parked) if tid is not None]
    try:
        statuses = port.get_task_statuses(task_ids)
    except Exception:  # noqa: BLE001 — a batch failure must not abort the sweep
        logger.exception(
            "Poller failed fetching task statuses for %d parked event(s); "
            "all left parked for the next pass",
            len(parked),
        )
        return 0

    requeued = 0
    for event in parked:
        try:
            requeued += _process_parked_event(port, event, statuses)
        except Exception:  # noqa: BLE001 — one bad event must not abort the sweep
            # A requeue can raise (network blip, malformed row). Isolate per
            # event: log and continue so the remaining parked events are
            # still evaluated this pass instead of being stranded until the
            # next poll because an earlier event blew up the loop.
            logger.exception(
                "Poller failed on event %s; left parked for the next pass",
                event.get("id"),
            )

    return requeued


def _process_parked_event(port: PollerPort, event: dict[str, Any], statuses: dict[str, str]) -> int:
    """Evaluate one parked event against pre-fetched statuses; return 1 if requeued, else 0.

    A requeue only counts when ``requeue_event`` confirms the transition
    (returns ``True``). A ``False`` return means the event was *not* moved to
    ``pending`` — e.g. it vanished or lost its parked state under a concurrent
    writer — so counting it would over-report progress and mask a stuck event.
    """
    task_id = _blocking_task_id(event)
    if task_id is None:
        return 0

    task_status = statuses.get(task_id)
    if task_status is None:
        logger.debug(
            "Event %s blocked on task %s which is not in task_queue — left parked",
            event["id"],
            task_id,
        )
        return 0
    reason_clause = _REQUEUE_REASONS.get(task_status)
    if reason_clause is None:
        # ``running`` / ``pending`` / ``claimed`` → not terminal; leave parked.
        return 0

    ok = port.requeue_event(event["id"], reason=f"Blocking task {task_id} {reason_clause}")
    if not ok:
        logger.warning(
            "Requeue of event %s (task %s %s) was not applied",
            event["id"],
            task_id,
            task_status,
        )
        return 0
    logger.info(
        "Re-queued event %s (blocking task %s is %s — orchestrator re-routes)",
        event["id"],
        task_id,
        task_status,
    )
    return 1


# -- Helpers ------------------------------------------------------------------


def _blocking_task_id(event: dict[str, Any]) -> str | None:
    """Extract ``blocked_by_task_id`` from an event's ``payload``.

    Handles payload as a dict or a JSON string (PostgREST may return
    jsonb as a pre-parsed dict or as a string depending on the query path).
    Returns ``None`` when the field is missing, empty, or unparseable.
    """
    payload = event.get("payload")
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    tid = payload.get("blocked_by_task_id")
    # Guard on None/empty-string explicitly, not truthiness: a valid task id of
    # integer ``0`` (or any falsy-but-present value) must survive — ``if tid``
    # would drop it and silently strand the parked event.
    if tid is None or tid == "":
        return None
    return str(tid)
