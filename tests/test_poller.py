"""Tests for the parked-event re-queue poller (#745 — Path B).

The poller re-queues ``parked`` events when their blocking task reaches a
terminal state (``done``, ``failed``, or ``parked``). It is a pure function
over a :class:`PollerPort` interface — every test uses an in-memory fake so
no live database is required.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents import poller


# ---------------------------------------------------------------------------
# FSM-faithful fake
# ---------------------------------------------------------------------------


class FakePollerPort:
    """In-memory :class:`PollerPort` holding both events and tasks.

    Events and tasks are plain dicts. The fake mirrors the subset of each
    FSM that the poller observes — it does not enforce state transitions
    (that is the real database's job).
    """

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        tasks: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events: list[dict[str, Any]] = events or []
        self.tasks: list[dict[str, Any]] = tasks or []
        self.requeue_calls: list[tuple[str, str]] = []  # (event_id, reason)

    # -- PollerPort surface --------------------------------------------------

    def find_parked_events(self) -> list[dict[str, Any]]:
        """Return parked events whose payload has a ``blocked_by_task_id`` key."""
        result: list[dict[str, Any]] = []
        for ev in self.events:
            if ev.get("state") != "parked":
                continue
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            tid = payload.get("blocked_by_task_id")
            # Mirror the production guard (_blocking_task_id): present-but-falsy
            # ids such as int 0 must survive — a truthiness test would drop them
            # and silently strand the event.
            if tid is not None and tid != "":
                result.append(ev)
        return result

    def get_task_status(self, task_id: str) -> str | None:
        for t in self.tasks:
            if t.get("id") == task_id:
                return t.get("status")
        return None

    def requeue_event(self, event_id: str, *, reason: str) -> bool:
        for ev in self.events:
            if ev["id"] == event_id:
                if ev["state"] != "parked":
                    return False
                ev["state"] = "pending"
                self.requeue_calls.append((event_id, reason))
                return True
        return False

    # -- test helpers --------------------------------------------------------

    def state_of(self, event_id: str) -> str:
        return next(ev["state"] for ev in self.events if ev["id"] == event_id)

    def task_status(self, task_id: str) -> str | None:
        return self.get_task_status(task_id)


def _ev(
    eid: str,
    task_id: str | None = None,
    state: str = "parked",
) -> dict[str, Any]:
    """Build an event dict. ``task_id`` is stored in ``payload.blocked_by_task_id``."""
    ev: dict[str, Any] = {"id": eid, "state": state}
    if task_id is not None:
        ev["payload"] = {"blocked_by_task_id": task_id}
    else:
        ev["payload"] = {}
    return ev


def _task(tid: str, status: str) -> dict[str, Any]:
    return {"id": tid, "status": status}


# ===========================================================================
# AC1: done task → event requeued
# ===========================================================================


class TestBlockingTaskDone:
    """When the blocking task reaches ``done``, the event is re-queued."""

    def test_requeues_parked_event_when_task_is_done(self):
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "done")],
        )
        n = poller.poll(port)
        assert n == 1
        assert port.state_of("e1") == "pending"
        assert len(port.requeue_calls) == 1
        assert port.requeue_calls[0][0] == "e1"
        assert "completed" in port.requeue_calls[0][1]

    def test_requeues_multiple_events_when_all_tasks_done(self):
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1"), _ev("e2", task_id="t2")],
            tasks=[_task("t1", "done"), _task("t2", "done")],
        )
        n = poller.poll(port)
        assert n == 2
        assert port.state_of("e1") == "pending"
        assert port.state_of("e2") == "pending"


# ===========================================================================
# AC2: running task → event stays parked
# ===========================================================================


class TestBlockingTaskStillRunning:
    """A task that is still ``running`` leaves the event parked."""

    def test_does_not_requeue_when_task_still_running(self):
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "running")],
        )
        n = poller.poll(port)
        assert n == 0
        assert port.state_of("e1") == "parked"
        assert port.requeue_calls == []


# ===========================================================================
# AC2b: parked task is terminal → event requeued (#964 MAJOR #1)
# ===========================================================================


class TestBlockingTaskParked:
    """A ``parked`` blocking task is terminal — the event must be re-queued.

    ``parked`` is in ``task_queue._TERMINAL_STATES`` and the FSM blocks any
    transition out of it, so a parked task never advances to ``done``. Leaving
    the event parked would strand it forever; the poller releases it so the
    orchestrator can re-route.
    """

    def test_requeues_event_when_task_parked(self):
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "parked")],
        )
        n = poller.poll(port)
        assert n == 1
        assert port.state_of("e1") == "pending"
        assert len(port.requeue_calls) == 1
        assert "parked" in port.requeue_calls[0][1]


# ===========================================================================
# AC3: failed task → event requeued (not silently dropped)
# ===========================================================================


class TestBlockingTaskFailed:
    """A ``failed`` task causes the event to be re-queued for the orchestrator."""

    def test_requeues_event_when_task_failed(self):
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "failed")],
        )
        n = poller.poll(port)
        assert n == 1
        assert port.state_of("e1") == "pending"
        assert len(port.requeue_calls) == 1
        assert "failed" in port.requeue_calls[0][1]

    def test_mixed_states_only_requeues_terminal(self):
        """Terminal tasks (done/failed/parked) requeue; only running stays."""
        port = FakePollerPort(
            events=[
                _ev("e-done", task_id="t-done"),
                _ev("e-failed", task_id="t-failed"),
                _ev("e-running", task_id="t-running"),
                _ev("e-parked", task_id="t-parked"),
            ],
            tasks=[
                _task("t-done", "done"),
                _task("t-failed", "failed"),
                _task("t-running", "running"),
                _task("t-parked", "parked"),
            ],
        )
        n = poller.poll(port)
        assert n == 3  # done + failed + parked (all terminal)
        assert port.state_of("e-done") == "pending"
        assert port.state_of("e-failed") == "pending"
        assert port.state_of("e-parked") == "pending"
        assert port.state_of("e-running") == "parked"  # only non-terminal stays


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Events without blocked_by_task_id, missing tasks, empty queue."""

    def test_no_blocking_task_id_is_skipped(self):
        """Parked event without ``blocked_by_task_id`` in payload is skipped."""
        port = FakePollerPort(
            events=[
                _ev("e-no-ref", task_id=None),  # no blocked_by_task_id
            ],
        )
        n = poller.poll(port)
        assert n == 0
        assert port.state_of("e-no-ref") == "parked"

    def test_unknown_task_id_is_skipped(self):
        """When the referenced task does not exist, the event stays parked."""
        port = FakePollerPort(
            events=[_ev("e1", task_id="nonexistent")],
            tasks=[],  # no matching task
        )
        n = poller.poll(port)
        assert n == 0
        assert port.state_of("e1") == "parked"

    def test_empty_parked_queue_is_a_noop(self):
        port = FakePollerPort(events=[], tasks=[])
        assert poller.poll(port) == 0

    def test_non_parked_events_are_ignored(self):
        """Only parked events are evaluated — pending/claimed/processed are skipped."""
        port = FakePollerPort(
            events=[
                _ev("e-pending", task_id="t1", state="pending"),
                _ev("e-claimed", task_id="t2", state="claimed"),
                _ev("e-processed", task_id="t3", state="processed"),
            ],
            tasks=[
                _task("t1", "done"),
                _task("t2", "done"),
                _task("t3", "done"),
            ],
        )
        n = poller.poll(port)
        assert n == 0  # no parked events with blocked_by_task_id

    def test_requeue_event_returns_false_for_nonexistent_event(self):
        port = FakePollerPort(events=[], tasks=[])
        result = port.requeue_event("no-such-event", reason="test")
        assert result is False

    def test_unknown_task_status_leaves_event_parked(self):
        """A novel task status (e.g. future ``cancelled``) leaves the event parked."""
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "cancelled")],
        )
        n = poller.poll(port)
        assert n == 0
        assert port.state_of("e1") == "parked"

    def test_json_string_payload_requeues_through_poll(self):
        """PostgREST JSON-string payload is parsed end-to-end through ``poll()``."""
        json_event = {
            "id": "e-json",
            "state": "parked",
            "payload": json.dumps({"blocked_by_task_id": "t9"}),
        }

        class _JsonStringPayloadPort(FakePollerPort):
            def find_parked_events(self) -> list[dict[str, Any]]:
                return [json_event]

        port = _JsonStringPayloadPort(
            events=[json_event],  # needed for requeue_event lookup
            tasks=[_task("t9", "done")],
        )
        n = poller.poll(port)
        assert n == 1
        assert len(port.requeue_calls) == 1
        assert port.requeue_calls[0][0] == "e-json"

    def test_requeue_already_pending_event_returns_false(self):
        """FakePollerPort mirrors production: requeue only from parked state."""
        port = FakePollerPort(
            events=[_ev("e1", task_id="t1", state="pending")],
            tasks=[_task("t1", "done")],
        )
        result = port.requeue_event("e1", reason="test")
        assert result is False


# ===========================================================================
# _blocking_task_id — payload shapes (#964 MINOR #6/#7)
# ===========================================================================


class TestBlockingTaskIdParsing:
    """Direct tests for the payload extractor across dict / JSON-string shapes."""

    def test_preserves_integer_zero(self):
        """A falsy-but-present task id (int ``0``) must survive, not be dropped."""
        ev = {"id": "e1", "state": "parked", "payload": {"blocked_by_task_id": 0}}
        assert poller._blocking_task_id(ev) == "0"

    def test_empty_string_task_id_is_none(self):
        ev = {"id": "e1", "state": "parked", "payload": {"blocked_by_task_id": ""}}
        assert poller._blocking_task_id(ev) is None

    def test_missing_task_id_is_none(self):
        ev = {"id": "e1", "state": "parked", "payload": {"other": "x"}}
        assert poller._blocking_task_id(ev) is None

    def test_parses_json_string_payload(self):
        """PostgREST may hand back jsonb as a string — it must be parsed."""
        ev = {
            "id": "e1",
            "state": "parked",
            "payload": json.dumps({"blocked_by_task_id": "t9"}),
        }
        assert poller._blocking_task_id(ev) == "t9"

    def test_malformed_json_string_is_none(self):
        ev = {"id": "e1", "state": "parked", "payload": "{not valid json"}
        assert poller._blocking_task_id(ev) is None

    def test_json_string_non_object_is_none(self):
        """A JSON string that decodes to a non-dict (e.g. a list) is skipped."""
        ev = {"id": "e1", "state": "parked", "payload": json.dumps([1, 2, 3])}
        assert poller._blocking_task_id(ev) is None


# ===========================================================================
# Robustness: per-event isolation + confirmed-requeue counting (#964 MAJOR #3/#4)
# ===========================================================================


class _RaisingStatusPort(FakePollerPort):
    """Fake whose status probe raises for one specific task id."""

    def __init__(self, *, raise_for: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._raise_for = raise_for

    def get_task_status(self, task_id: str) -> str | None:
        if task_id == self._raise_for:
            raise RuntimeError("status probe blew up")
        return super().get_task_status(task_id)


class _FalseRequeuePort(FakePollerPort):
    """Fake whose ``requeue_event`` reports the transition was NOT applied."""

    def requeue_event(self, event_id: str, *, reason: str) -> bool:
        return False


class _RaisingRequeuePort(FakePollerPort):
    """Fake whose ``requeue_event`` raises for one specific event id."""

    def __init__(self, *, raise_for: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._raise_for = raise_for

    def requeue_event(self, event_id: str, *, reason: str) -> bool:
        if event_id == self._raise_for:
            raise RuntimeError("requeue blew up")
        return super().requeue_event(event_id, reason=reason)


class _RaisingFindPort(FakePollerPort):
    """Fake whose ``find_parked_events`` raises — simulates a query outage."""

    def find_parked_events(self) -> list[dict[str, Any]]:
        raise RuntimeError("find_parked_events blew up")


class TestPollRobustness:
    def test_one_bad_event_does_not_abort_the_sweep(self):
        """A probe failure on one parked event must not strand the rest."""
        port = _RaisingStatusPort(
            raise_for="t-bad",
            events=[_ev("e-bad", task_id="t-bad"), _ev("e-good", task_id="t-good")],
            tasks=[_task("t-bad", "done"), _task("t-good", "done")],
        )
        n = poller.poll(port)
        assert n == 1  # the good event still requeued
        assert port.state_of("e-good") == "pending"
        assert port.state_of("e-bad") == "parked"  # left parked, not lost

    def test_unconfirmed_requeue_is_not_counted(self):
        """``requeue_event`` returning False means no transition → don't count it."""
        port = _FalseRequeuePort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "done")],
        )
        n = poller.poll(port)
        assert n == 0

    def test_unconfirmed_requeue_for_failed_task_is_not_counted(self):
        port = _FalseRequeuePort(
            events=[_ev("e1", task_id="t1")],
            tasks=[_task("t1", "failed")],
        )
        assert poller.poll(port) == 0

    def test_requeue_raising_on_one_event_does_not_strand_the_rest(self):
        """A ``requeue_event`` failure on one event must not abort the sweep."""
        port = _RaisingRequeuePort(
            raise_for="e-bad",
            events=[_ev("e-bad", task_id="t-bad"), _ev("e-good", task_id="t-good")],
            tasks=[_task("t-bad", "done"), _task("t-good", "done")],
        )
        n = poller.poll(port)
        assert n == 1  # the good event still requeued
        assert port.state_of("e-good") == "pending"
        assert port.state_of("e-bad") == "parked"  # left parked, not lost

    def test_find_parked_events_failure_propagates_to_caller(self):
        """``poll()`` does not swallow a ``find_parked_events`` outage.

        The whole sweep is wrapped in a try/except by the wake_driver's tick
        (Step 2b) so a query outage retries next tick — but ``poll()`` itself
        surfaces the failure rather than reporting a false ``0`` requeued.
        """
        port = _RaisingFindPort()
        with pytest.raises(RuntimeError):
            poller.poll(port)


# ===========================================================================
# Wake-driver integration: tick calls the poller
# ===========================================================================


class _MinimalFakeEventQueue:
    """Minimal EventQueuePort stub for the integration test.

    ``claim_next`` returns ``None`` (empty pool) so the drain is a no-op
    and we can focus on the poller step.
    """

    def __init__(self) -> None:
        self.wake_signals: list[bool] = []

    def claim_next(self) -> None:
        return None

    def mark_processed(self, event_id: str, *, action: str = "") -> bool:
        return False

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        return 0

    def wait_for_wake(self, *, timeout_seconds: float | None) -> bool:
        if self.wake_signals:
            return self.wake_signals.pop(0)
        return False


def test_tick_calls_poller_when_port_provided():
    """``tick()`` re-queues parked events when ``poller_port`` is given."""
    from agents import wake_driver

    poller_port = FakePollerPort(
        events=[_ev("e1", task_id="t1")],
        tasks=[_task("t1", "done")],
    )
    event_q = _MinimalFakeEventQueue()

    result = wake_driver.tick(
        event_q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        poller_port=poller_port,
    )

    assert result.requeued == 1
    assert poller_port.state_of("e1") == "pending"


def test_tick_skips_poller_when_port_is_none():
    """``tick()`` without ``poller_port`` works as before (backward compat)."""
    from agents import wake_driver

    q = _MinimalFakeEventQueue()
    result = wake_driver.tick(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
    )

    assert result.requeued == 0
    assert result.reclaimed == 0
    assert result.processed == 0


class _RaisingPollerPort:
    """A poller port whose sweep always raises — simulates a poller outage."""

    def find_parked_events(self) -> list[dict[str, Any]]:
        raise RuntimeError("poller down")

    def get_task_status(self, task_id: str) -> str | None:
        return None

    def requeue_event(self, event_id: str, *, reason: str) -> bool:
        return False


class _OneEventQueue(_MinimalFakeEventQueue):
    """Yields a single pending event once, then drains empty."""

    def __init__(self) -> None:
        super().__init__()
        self._pending: list[dict[str, Any]] = [{"id": "ev-1"}]
        self.processed_ids: list[str] = []

    def claim_next(self) -> dict[str, Any] | None:
        if self._pending:
            return self._pending.pop(0)
        return None

    def mark_processed(self, event_id: str, *, action: str = "") -> bool:
        self.processed_ids.append(event_id)
        return True


def test_tick_poller_exception_does_not_skip_event_drain():
    """CRITICAL #2: a poller blow-up must not abort the event drain (Step 3).

    Without the try/except around the poll step, the raise propagates out of
    ``tick()`` before ``drain_pending`` runs — stranding every event claimed
    this pass. The drain is the primary wake path and must survive a poller
    outage.
    """
    from agents import wake_driver

    q = _OneEventQueue()
    result = wake_driver.tick(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        poller_port=_RaisingPollerPort(),
    )

    assert result.processed == 1  # event drained despite the poller failure
    assert q.processed_ids == ["ev-1"]
    assert result.requeued == 0  # poller produced nothing


def test_run_forwards_poller_port_to_tick():
    """MAJOR #5: ``run()`` must thread ``poller_port`` through to each ``tick()``."""
    from agents import wake_driver

    poller_port = FakePollerPort(
        events=[_ev("e1", task_id="t1")],
        tasks=[_task("t1", "done")],
    )
    q = _MinimalFakeEventQueue()
    q.wake_signals = [True]

    calls = {"n": 0}

    def should_continue() -> bool:
        calls["n"] += 1
        return calls["n"] <= 1  # exactly one tick

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        should_continue=should_continue,
        poller_port=poller_port,
    )

    # The single tick forwarded poller_port and requeued the parked event.
    assert poller_port.state_of("e1") == "pending"
    assert poller_port.requeue_calls
    assert poller_port.requeue_calls[0][0] == "e1"
