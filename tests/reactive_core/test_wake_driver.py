"""Tests for the wake_driver loop (#743).

The wake_driver is the crash-safe, event-driven loop that replaces the
retired APScheduler resident scheduler. It owns no decisions — only the
wake mechanics: LISTEN for a NOTIFY wake signal, cold-boot the (injected)
orchestrator for the next single ``pending`` event, drain one at a time,
and let a watchdog re-claim rows stranded in ``claimed`` by a dead tick.

These tests exercise the loop's *external behavior* through the
``EventQueuePort`` interface with an FSM-faithful fake — the same approach
the issue's Testing Decisions call for ("fixed event inputs, no live
model"). The psycopg-backed adapter is an integration seam and is not
unit-tested here (no live DB).
"""

from __future__ import annotations

import inspect
import logging
import subprocess
import tomllib
import types
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from agents import task_dispatch as td
from agents import wake_driver
from agents.task_dispatch import TaskQueuePort, TrackedProc

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# --- FSM-faithful fake -----------------------------------------------------

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class FakeEventQueue:
    """In-memory model of the #739 events FSM, behind the EventQueuePort.

    Mirrors the Postgres RPCs: ``claim_next`` (highest-severity pending,
    pending→claimed), ``mark_processed`` (claimed→processed),
    ``reclaim_stale`` (claimed→pending for rows older than a threshold).
    A monotonically-advanceable ``clock`` stands in for ``now()`` so the
    watchdog threshold can be tested deterministically.
    """

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events: list[dict] = events or []
        self.clock: float = 0.0
        # Scripted wake signals consumed by wait_for_wake (True=NOTIFY).
        self.wake_signals: list[bool] = []
        self.processed_calls: list[str] = []
        self.parked_calls: list[tuple[str, str]] = []

    # -- EventQueuePort surface --------------------------------------------

    def claim_next(self) -> dict | None:
        pending = [e for e in self.events if e["state"] == "pending"]
        if not pending:
            return None
        pending.sort(key=lambda e: (_SEVERITY_RANK.get(e.get("severity", "info"), 4), e["id"]))
        row = pending[0]
        row["state"] = "claimed"
        row["claimed_at"] = self.clock
        return dict(row)

    def mark_processed(self, event_id: str, *, action: str = "") -> bool:
        for e in self.events:
            if e["id"] == event_id and e["state"] == "claimed":
                e["state"] = "processed"
                self.processed_calls.append(event_id)
                return True
        return False

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        count = 0
        for e in self.events:
            if e["state"] == "claimed" and (self.clock - e["claimed_at"]) >= older_than_seconds:
                e["state"] = "pending"
                e["claimed_at"] = None
                count += 1
        return count

    def wait_for_wake(self, *, timeout_seconds: float | None) -> bool:
        if self.wake_signals:
            return self.wake_signals.pop(0)
        return False

    def park(self, event_id: str, *, reason: str = "") -> bool:
        for e in self.events:
            if e["id"] == event_id and e["state"] == "claimed":
                e["state"] = "parked"
                e["action_taken"] = reason
                self.parked_calls.append((event_id, reason))
                return True
        return False

    def recent_events(self, *, limit: int) -> list[dict]:
        return [dict(e) for e in self.events[:limit]]

    # -- test helpers ------------------------------------------------------

    def state_of(self, event_id: str) -> str:
        return next(e["state"] for e in self.events if e["id"] == event_id)


def _ev(eid: str, severity: str = "info", state: str = "pending") -> dict:
    return {"id": eid, "severity": severity, "state": state, "claimed_at": None}


# --- AC1 / AC2: drain one at a time, advance immediately -------------------


def test_drain_pending_processes_every_pending_event_once():
    q = FakeEventQueue([_ev("a"), _ev("b"), _ev("c")])

    processed = wake_driver.drain_pending(q, wake_driver.default_orchestrator)

    assert processed == 3
    assert q.processed_calls == ["a", "b", "c"]
    assert all(e["state"] == "processed" for e in q.events)


def test_drain_pending_claims_highest_severity_first():
    q = FakeEventQueue([_ev("lo", "low"), _ev("crit", "critical"), _ev("med", "medium")])

    wake_driver.drain_pending(q, wake_driver.default_orchestrator)

    # Severity order, not insertion order — proves claim_next ordering is honored.
    assert q.processed_calls == ["crit", "med", "lo"]


def test_drain_pending_empty_queue_is_a_noop():
    q = FakeEventQueue([])
    assert wake_driver.drain_pending(q, wake_driver.default_orchestrator) == 0


# --- #1385 AC-C: poison-pill handling — park after the second failure ------


def test_drain_pending_first_failure_reraises_and_leaves_claimed():
    q = FakeEventQueue([_ev("boom")])
    failed_events: dict[str, int] = {}

    def crashing(event: dict) -> None:
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        wake_driver.drain_pending(q, crashing, failed_events=failed_events)

    assert q.state_of("boom") == "claimed"
    assert failed_events["boom"] == 1
    assert q.parked_calls == []


def test_drain_pending_second_failure_same_event_id_parks_and_continues():
    q = FakeEventQueue([_ev("boom"), _ev("ok")])
    # Simulates a prior tick already having failed once on "boom".
    failed_events: dict[str, int] = {"boom": 1}

    def crash_on_boom(event: dict) -> None:
        if event["id"] == "boom":
            raise ValueError("still broken")

    processed = wake_driver.drain_pending(q, crash_on_boom, failed_events=failed_events)

    assert q.state_of("boom") == "parked"
    assert q.parked_calls == [("boom", "poison-pill: ValueError: still broken")]
    # Drain continued past the poison event instead of aborting the tick.
    assert q.state_of("ok") == "processed"
    assert processed == 1


def test_drain_pending_without_failed_events_kwarg_still_reraises_once():
    # Back-compat: callers that don't pass failed_events (pre-#1385 call sites)
    # get a fresh per-call dict — a first failure still re-raises.
    q = FakeEventQueue([_ev("boom")])

    def crashing(event: dict) -> None:
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        wake_driver.drain_pending(q, crashing)

    assert q.state_of("boom") == "claimed"


def test_run_parks_poison_pill_event_after_second_tick_failure():
    q = FakeEventQueue([_ev("boom")])
    calls = {"n": 0}

    def always_crash(event: dict) -> None:
        calls["n"] += 1
        raise RuntimeError("poison")

    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        if ticks["n"] == 2:
            # Advance the clock past the watchdog threshold so tick 2's
            # watchdog step reclaims the row stranded by tick 1's failure.
            q.clock = 400.0
        return ticks["n"] <= 2

    q.wake_signals = [True, True]

    wake_driver.run(
        q,
        always_crash,
        stale_after_seconds=300,
        should_continue=should_continue,
    )

    assert q.state_of("boom") == "parked"
    assert q.parked_calls == [("boom", "poison-pill: RuntimeError: poison")]
    assert calls["n"] == 2


def test_drain_advances_to_next_without_a_fixed_interval():
    # AC2: one drain call empties the queue in a single pass — no per-event
    # sleep / interval. We assert the orchestrator saw every event in order
    # within one synchronous drain (a fixed-interval poller could not).
    seen: list[str] = []
    q = FakeEventQueue([_ev("e1"), _ev("e2"), _ev("e3")])

    wake_driver.drain_pending(q, lambda event: seen.append(event["id"]))

    assert seen == ["e1", "e2", "e3"]


# --- AC1: a wake signal cold-boots a tick ----------------------------------


def test_run_drains_on_a_wake_signal_then_stops():
    q = FakeEventQueue([_ev("x"), _ev("y")])
    q.wake_signals = [True]  # one NOTIFY, then should_continue stops the loop.
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        return ticks["n"] <= 1

    wake_driver.run(q, wake_driver.default_orchestrator, should_continue=should_continue)

    assert q.processed_calls == ["x", "y"]


def test_run_runs_watchdog_each_tick_even_without_a_notify():
    # A timeout (no NOTIFY) still triggers a tick so the watchdog runs.
    q = FakeEventQueue([_ev("stuck", state="claimed")])
    q.events[0]["claimed_at"] = 0.0
    q.clock = 999.0  # well past the threshold
    q.wake_signals = [False]  # timeout, not a NOTIFY
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        return ticks["n"] <= 1

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        should_continue=should_continue,
    )

    # Stale claimed row was reclaimed → re-drained → processed in the same tick.
    assert q.state_of("stuck") == "processed"


# --- AC3: crash-safety — killed mid-tick → reprocessed, never lost ---------


def test_event_left_claimed_when_orchestrator_dies_midtick():
    q = FakeEventQueue([_ev("boom")])

    def crashing(event: dict) -> None:
        raise RuntimeError("simulated kill mid-tick")

    with pytest.raises(RuntimeError):
        wake_driver.drain_pending(q, crashing)

    # mark_processed never ran: the row is stranded in 'claimed', not lost,
    # and crucially not 'processed'.
    assert q.state_of("boom") == "claimed"
    assert q.processed_calls == []


def test_stranded_event_is_reprocessed_after_watchdog_reclaim():
    q = FakeEventQueue([_ev("boom")])

    def crashing(event: dict) -> None:
        raise RuntimeError("simulated kill mid-tick")

    with pytest.raises(RuntimeError):
        wake_driver.drain_pending(q, crashing)
    assert q.state_of("boom") == "claimed"

    # Watchdog runs after the threshold elapses; the row returns to pending.
    q.clock = 400.0
    reclaimed = wake_driver.run_watchdog(q, stale_after_seconds=300)
    assert reclaimed == 1
    assert q.state_of("boom") == "pending"

    # A healthy re-drain now processes it — at-least-once delivery.
    processed = wake_driver.drain_pending(q, wake_driver.default_orchestrator)
    assert processed == 1
    assert q.state_of("boom") == "processed"


# --- AC4: watchdog re-claims a stale claimed row ---------------------------


def test_watchdog_reclaims_only_rows_past_the_threshold():
    fresh = _ev("fresh", state="claimed")
    stale = _ev("stale", state="claimed")
    q = FakeEventQueue([fresh, stale])
    q.events[0]["claimed_at"] = 350.0  # fresh: claimed recently
    q.events[1]["claimed_at"] = 0.0  # stale: claimed long ago
    q.clock = 400.0

    reclaimed = wake_driver.run_watchdog(q, stale_after_seconds=300)

    assert reclaimed == 1
    assert q.state_of("stale") == "pending"
    assert q.state_of("fresh") == "claimed"


def test_watchdog_noop_when_nothing_is_stale():
    q = FakeEventQueue([_ev("c", state="claimed")])
    q.events[0]["claimed_at"] = 100.0
    q.clock = 200.0
    assert wake_driver.run_watchdog(q, stale_after_seconds=300) == 0


def test_tick_reclaims_then_drains():
    q = FakeEventQueue([_ev("stale", state="claimed"), _ev("new")])
    q.events[0]["claimed_at"] = 0.0
    q.clock = 400.0

    result = wake_driver.tick(q, wake_driver.default_orchestrator, stale_after_seconds=300)

    assert result.reclaimed == 1
    assert result.processed == 2  # the reclaimed row + the new pending row
    assert all(e["state"] == "processed" for e in q.events)


# --- AC6: no resident-process / `while True: sleep` loop -------------------


def test_wake_driver_has_no_busy_sleep_poll_loop():
    src = inspect.getsource(wake_driver)
    # The retired scheduler used `while True:` + `time.sleep(...)` as a
    # resident interval poller. wake_driver must be event-driven instead.
    assert "while True" not in src, "wake_driver must not contain a `while True` resident loop"

    # _call_with_retry's bounded, cold-boot-only backoff (startup DB/network
    # readiness race fix) is the one deliberate exception: a finite number of
    # attempts during startup only, not a resident poll. Everything else must
    # stay event-driven (block on the NOTIFY socket).
    retry_helper_src = inspect.getsource(wake_driver._call_with_retry)
    src_outside_retry_helper = src.replace(retry_helper_src, "")
    assert "time.sleep(" not in src_outside_retry_helper, (
        "wake_driver must not busy-sleep outside the bounded startup retry helper; "
        "the run loop blocks on the NOTIFY socket"
    )


# --- AC5: scheduler.py + apscheduler dep + installer retired ---------------


def test_scheduler_module_is_retired():
    assert not (_REPO_ROOT / "agents" / "scheduler.py").exists(), (
        "agents/scheduler.py must be deleted — wake_driver replaces the APScheduler scheduler"
    )


def test_scheduler_service_installer_is_retired():
    assert not (_REPO_ROOT / "scripts" / "install" / "install-scheduler-service.ps1").exists(), (
        "install-scheduler-service.ps1 must be deleted — the resident scheduler service is retired"
    )


def test_apscheduler_and_sqlalchemy_dropped_from_deps():
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    agents_deps = pyproject["project"]["optional-dependencies"]["agents"]
    joined = " ".join(agents_deps).lower()
    assert "apscheduler" not in joined, "apscheduler dep must be dropped with scheduler.py"
    assert "sqlalchemy" not in joined, (
        "sqlalchemy was only APScheduler's jobstore driver — drop it too"
    )


# --- #909 AC1: tick gains task reclaim + task drain, four ordered steps -----


class _RecordingTaskQueue:
    """Minimal TaskQueuePort that logs the calls drain/reclaim make, in order.

    Shares the order log with a _LoggingEventQueue so a single tick's
    event-side and task-side operations can be asserted against AC1's
    ``reclaim(events) → reclaim_tasks() → drain(events) → drain_tasks()`` order.
    """

    def __init__(self, log: list, *, pending=None, stale_claimed: int = 0, stale_running=None):
        self._log = log
        self._pending = list(pending or [])
        self._stale_claimed = stale_claimed
        self._stale_running = list(stale_running or [])
        self.transitions: list[tuple[str, str, str | None]] = []
        self.statuses: dict[str, str] = {}

    def claim_next(self, *, assignee: str):
        for i, r in enumerate(self._pending):
            if r.get("assignee", "sandcastle") == assignee:
                self._log.append("task_drain")
                return self._pending.pop(i)
        return None

    def count_running(self, *, assignee: str) -> int:
        return 0

    def transition(self, task_id: str, to_status: str, *, reason=None):
        self._log.append(f"task_transition:{to_status}")
        self.transitions.append((task_id, to_status, reason))
        return {"id": task_id, "status": to_status}

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        self._log.append("task_reclaim")
        return self._stale_claimed

    def list_stale_running(self, *, assignee: str, older_than_seconds: float):
        self._log.append("task_list_running")
        return list(self._stale_running)

    def requeue_running(self, task_id: str) -> bool:
        self._log.append("task_requeue")
        return True

    def get_status(self, task_id: str) -> str | None:
        return self.statuses.get(task_id)


def test_recording_task_queue_conforms_to_the_port_protocol():
    # review #957-2 (MAJOR): the fake must satisfy the full TaskQueuePort
    # surface (incl. requeue_running, #921 AC4) — a partial fake lets tick
    # paths that touch the missing method blow up only in production.
    assert isinstance(_RecordingTaskQueue([]), TaskQueuePort)


class _LoggingEventQueue(FakeEventQueue):
    """FakeEventQueue that records reclaim/drain into a shared order log."""

    def __init__(self, log: list, events=None):
        super().__init__(events)
        self._log = log

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        self._log.append("event_reclaim")
        return super().reclaim_stale(older_than_seconds=older_than_seconds)

    def claim_next(self):
        row = super().claim_next()
        if row is not None:
            self._log.append("event_drain")
        return row


class _HealthyUsage:
    """UsageReading-shaped fake — quota fine, drain proceeds (#921 AC4)."""

    near_exhaustion = False


def _healthy_usage() -> _HealthyUsage:
    return _HealthyUsage()


class _TickProc:
    """Popen-shaped fake with a scripted return code — never a real process.

    Tests that hand live (``rc=None``) instances to ``tick`` must also inject
    ``task_clock`` (and rely on the injected ``task_kill``) so the runaway
    killer never reaches a real ``taskkill`` against the fake pid.
    """

    def __init__(self, rc: int | None = None, pid: int = 4242) -> None:
        self._rc = rc
        self.pid = pid

    def poll(self) -> int | None:
        return self._rc


class _SpawnHandle:
    """SpawnResult-shaped fake exposing a pollable ``proc`` (not throttled)."""

    def __init__(self, proc: _TickProc) -> None:
        self.proc = proc


def test_tick_runs_the_four_steps_in_order():
    log: list = []
    eq = _LoggingEventQueue(log, [_ev("e1", state="claimed")])
    eq.events[0]["claimed_at"] = 0.0
    eq.clock = 999.0  # the claimed event is stale → reclaimed → drained this tick
    tq = _RecordingTaskQueue(log, pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}])

    wake_driver.tick(
        eq,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: None,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )

    # AC1 order: reclaim(events) → reclaim_tasks() → drain(events) → drain_tasks()
    assert log.index("event_reclaim") < log.index("task_reclaim")
    # Within the task watchdog, claimed-reclaim precedes the running-reaper scan.
    assert log.index("task_reclaim") < log.index("task_list_running")
    assert log.index("task_list_running") < log.index("event_drain")
    assert log.index("event_drain") < log.index("task_drain")


def test_tick_without_task_port_is_event_only():
    # Backward-compat: omitting task_port skips both task steps entirely.
    q = FakeEventQueue([_ev("a")])
    result = wake_driver.tick(q, wake_driver.default_orchestrator, stale_after_seconds=300)
    assert result.processed == 1
    assert result.tasks_spawned == 0
    assert result.tasks_reclaimed == 0
    assert result.tasks_reaped == 0
    assert result.tasks_failed == 0
    assert result.tasks_done == 0
    assert result.tasks_failed_exit == 0
    assert result.worktrees_pruned == 0
    assert result.worktrees_retained == 0
    assert result.worktrees_ttl_pruned == 0
    assert result.worktrees_cap_evicted == 0


def test_tick_reports_task_counts():
    log: list = []
    eq = FakeEventQueue([])
    tq = _RecordingTaskQueue(
        log,
        pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}],
        stale_claimed=2,
        stale_running=[{"id": "r1"}],
    )
    result = wake_driver.tick(
        eq,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: None,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )
    assert result.tasks_reclaimed == 2  # AC5 stale claimed → pending
    assert result.tasks_reaped == 1  # AC6 stale running → failed
    assert result.tasks_spawned == 1  # AC2/AC3/AC4 the pending sandcastle row


# --- review #1: the task side and event side are isolated within a tick -----


class _RaisingOnReclaimTaskQueue:
    """TaskQueuePort whose task watchdog raises — models a Supabase outage in
    tick Step 2 (the task reclaim), which must not starve the event drain."""

    def claim_next(self, *, assignee: str):
        return None

    def count_running(self, *, assignee: str) -> int:
        return 0

    def transition(self, task_id: str, to_status: str, *, reason=None):
        return {"id": task_id, "status": to_status}

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        raise RuntimeError("supabase unreachable")

    def list_stale_running(self, *, assignee: str, older_than_seconds: float):
        return []


class _RaisingOnDrainTaskQueue:
    """TaskQueuePort whose drain raises — models a task-store outage in tick
    Step 4 (after events already drained in Step 3)."""

    def claim_next(self, *, assignee: str):
        raise RuntimeError("supabase unreachable")

    def count_running(self, *, assignee: str) -> int:
        return 0

    def transition(self, task_id: str, to_status: str, *, reason=None):
        return {"id": task_id, "status": to_status}

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        return 0

    def list_stale_running(self, *, assignee: str, older_than_seconds: float):
        return []


def test_tick_task_watchdog_failure_does_not_block_event_drain():
    # A Supabase outage in the task watchdog (Step 2) must not starve the
    # psycopg-backed event path (Step 3). Events still drain; task counts are
    # zero; the tick returns instead of raising.
    q = FakeEventQueue([_ev("a"), _ev("b")])
    result = wake_driver.tick(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=_RaisingOnReclaimTaskQueue(),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )
    assert result.processed == 2  # events drained despite the task-side outage
    assert result.tasks_reclaimed == 0
    assert result.tasks_reaped == 0


def test_tick_task_drain_failure_does_not_crash_tick():
    # A failure in the task drain (Step 4) is contained — the event drain
    # (Step 3) already ran, and the tick returns a result instead of raising.
    q = FakeEventQueue([_ev("a")])
    result = wake_driver.tick(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=_RaisingOnDrainTaskQueue(),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )
    assert result.processed == 1
    assert result.tasks_spawned == 0
    assert result.tasks_failed == 0


# --- review #3: run() forwards every task param to tick() -------------------


def test_run_forwards_task_spawn_and_resolver_to_tick():
    # run() must forward task_spawn / task_resolve_binary, else the loop
    # silently falls back to the production defaults (real claude binary, real
    # spawn) no matter what main() injected. Drive one iteration and assert the
    # injected fakes were the ones used.
    log: list = []
    q = FakeEventQueue([])
    q.wake_signals = [True]
    tq = _RecordingTaskQueue(
        log, pending=[{"id": "t1", "goal": "do-the-thing", "assignee": "sandcastle"}]
    )
    spawned: list[str] = []
    resolved = {"n": 0}
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        return ticks["n"] <= 1

    def fake_resolve() -> str:
        resolved["n"] += 1
        return "claude"

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        should_continue=should_continue,
        task_port=tq,
        task_spawn=lambda goal, **_: spawned.append(goal),
        task_resolve_binary=fake_resolve,
        task_read_usage=_healthy_usage,
    )

    assert spawned == ["do-the-thing"]  # injected spawn forwarded, not the default
    assert resolved["n"] == 1  # injected resolver forwarded, not the default


def test_run_forwards_task_thresholds_to_tick():
    # The claimed/running staleness thresholds must reach reclaim_stale_tasks;
    # a partial forward would silently apply the module defaults instead.
    seen: dict = {}

    class _ThresholdPort:
        def claim_next(self, *, assignee: str):
            return None

        def count_running(self, *, assignee: str) -> int:
            return 0

        def transition(self, task_id: str, to_status: str, *, reason=None):
            return {"id": task_id}

        def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
            seen["claimed"] = older_than_seconds
            return 0

        def list_stale_running(self, *, assignee: str, older_than_seconds: float):
            seen["running"] = older_than_seconds
            return []

    q = FakeEventQueue([])
    q.wake_signals = [False]
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        return ticks["n"] <= 1

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        should_continue=should_continue,
        task_port=_ThresholdPort(),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_claimed_stale_after_seconds=111,
        task_running_reap_after_seconds=222,
    )

    assert seen == {"claimed": 111, "running": 222}


# --- #1390 AC6: tick wires the on-disk task-worktree sweep (Step 2a) --------


def _init_git_repo(path) -> None:
    """A minimal real git repo with one commit on main, so a later
    ``_create_task_worktree`` has something to branch from."""
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=path, check=True)
    (path / "README.md").write_text("stub\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "init",
        ],
        cwd=path,
        check=True,
    )


def test_tick_sweeps_task_worktrees_when_task_port_present(tmp_path, monkeypatch):
    # AC6: Step 2a must actually run sweep_task_worktrees against the on-disk
    # tree when task_port is supplied, and fold its result into the
    # TickResult — the standalone function already has direct unit coverage
    # (test_agents_task_dispatch.py); this proves the wiring, not the logic.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setattr(td, "_REPO_ROOT", str(repo))
    td._create_task_worktree("orphan")  # no queue row below -> absent -> pruned

    q = FakeEventQueue([])
    tq = _RecordingTaskQueue([])  # statuses={} -> get_status("orphan") is None

    result = wake_driver.tick(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: None,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )

    assert result.worktrees_pruned == 1
    assert result.worktrees_retained == 0
    assert result.worktrees_ttl_pruned == 0
    assert result.worktrees_cap_evicted == 0
    assert not (repo / ".reactive" / "worktrees" / "orphan").exists()


def test_tick_worktree_sweep_failure_does_not_block_drains(monkeypatch):
    # A worktree-sweep outage (task-store or git) in Step 2a must not starve
    # the event drain (Step 3) or the task drain (Step 4) — same isolation
    # contract as the other task-side steps.
    def _boom(*_a, **_k):
        raise RuntimeError("git worktree prune unreachable")

    monkeypatch.setattr(wake_driver, "sweep_task_worktrees", _boom)

    log: list = []
    q = FakeEventQueue([_ev("a")])
    tq = _RecordingTaskQueue(log, pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}])

    result = wake_driver.tick(
        q,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: None,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )

    assert result.processed == 1  # event drain still ran
    assert "task_drain" in log  # task drain still ran
    assert result.worktrees_pruned == 0
    assert result.worktrees_retained == 0
    assert result.worktrees_ttl_pruned == 0
    assert result.worktrees_cap_evicted == 0


def test_run_forwards_task_worktree_params_to_tick(monkeypatch):
    # The worktree retention seconds/cap/clock knobs must reach
    # sweep_task_worktrees via tick — a partial forward would silently apply
    # the module defaults instead of what main() (or a test) injected.
    seen: dict = {}

    def _fake_sweep(port, *, retention_seconds, retention_cap, now):
        seen["retention_seconds"] = retention_seconds
        seen["retention_cap"] = retention_cap
        seen["now"] = now
        return td.WorktreeSweepResult(pruned=0, retained=0, ttl_pruned=0, cap_evicted=0)

    monkeypatch.setattr(wake_driver, "sweep_task_worktrees", _fake_sweep)

    def fake_now() -> float:
        return 12345.0

    q = FakeEventQueue([])
    q.wake_signals = [False]
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        return ticks["n"] <= 1

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        should_continue=should_continue,
        task_port=_RecordingTaskQueue([]),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_worktree_retention_seconds=111,
        task_worktree_retention_cap=7,
        task_worktree_now=fake_now,
    )

    assert seen["retention_seconds"] == 111
    assert seen["retention_cap"] == 7
    assert seen["now"] is fake_now


# --- #921 AC2/AC3/AC8: completion poll closes running→done in the tick ------


def test_tick_completion_poll_runs_before_watchdogs_and_drains():
    # AC3 ordering: Step 0 (completion poll) precedes the event watchdog, the
    # task watchdog, and both drains — so an exited child's slot is freed and
    # its row closed before this same tick reaps or re-claims anything.
    log: list = []
    eq = _LoggingEventQueue(log, [_ev("e1", state="claimed")])
    eq.events[0]["claimed_at"] = 0.0
    eq.clock = 999.0
    tq = _RecordingTaskQueue(log, pending=[{"id": "t2", "goal": "g", "assignee": "sandcastle"}])
    procs = {"t1": TrackedProc(proc=_TickProc(rc=0), started_at=0.0)}

    wake_driver.tick(
        eq,
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: None,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 0.0,
    )

    done_at = log.index("task_transition:done")
    assert done_at < log.index("event_reclaim")
    assert done_at < log.index("task_reclaim")
    assert done_at < log.index("task_drain")


def test_tick_reports_completion_counts_and_drops_closed_entries():
    # AC2/AC8: exit 0 → done, exit ≠0 → failed_exit; both dropped from the
    # map; a still-running entry is kept.
    log: list = []
    tq = _RecordingTaskQueue(log)
    procs = {
        "ok": TrackedProc(proc=_TickProc(rc=0), started_at=0.0),
        "bad": TrackedProc(proc=_TickProc(rc=3), started_at=0.0),
        "live": TrackedProc(proc=_TickProc(rc=None), started_at=0.0),
    }

    result = wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: None,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 0.0,
    )

    assert result.tasks_done == 1
    assert result.tasks_failed_exit == 1
    assert set(procs) == {"live"}
    assert ("ok", "done", None) in tq.transitions
    assert ("bad", "failed", "exit 3") in tq.transitions


def test_tick_shields_live_rows_from_the_orphan_reaper():
    # #921 AC5: a stale running row WITH a live tracked process is not reaped;
    # the stale row with no tracked process is an orphan → failed.
    log: list = []
    tq = _RecordingTaskQueue(log, stale_running=[{"id": "live"}, {"id": "orphan"}])
    procs = {"live": TrackedProc(proc=_TickProc(rc=None), started_at=0.0)}

    result = wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 0.0,
    )

    assert result.tasks_reaped == 1
    failed_ids = [t[0] for t in tq.transitions if t[1] == "failed"]
    assert failed_ids == ["orphan"]
    assert "live" in procs  # still tracked, still running


def test_tick_kills_runaway_live_processes():
    # #921 AC6: a live process past the reap threshold is tree-killed via the
    # injected kill, its row failed, the entry dropped — and it folds into
    # tasks_failed_exit.
    log: list = []
    tq = _RecordingTaskQueue(log)
    proc = _TickProc(rc=None)
    procs = {"runaway": TrackedProc(proc=proc, started_at=0.0)}
    killed: list = []

    result = wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 999_999.0,  # way past the 6h knob
        task_running_reap_after_seconds=60,
        task_kill=killed.append,
    )

    assert killed == [proc]
    assert result.tasks_failed_exit == 1
    assert procs == {}
    assert any(
        t[0] == "runaway" and t[1] == "failed" and "max runtime" in (t[2] or "")
        for t in tq.transitions
    )


def test_tick_merges_spawned_procs_into_the_tracking_map():
    # AC2: a successful spawn's (task_id, proc) pair lands in the map, stamped
    # with the injected clock — so the NEXT tick can poll it to completion. The
    # #953 spawn context (goal, idempotency_key, tz-aware spawned_at) must fold
    # through too, because the terminal completion poll reads it off TrackedProc
    # to compute PR evidence and lineage; asserting only proc/started_at would
    # let a regression that drops those fields pass unnoticed (MEDIUM #1011 r3).
    log: list = []
    proc = _TickProc(rc=None)
    tq = _RecordingTaskQueue(
        log,
        pending=[
            {
                "id": "t1",
                "goal": "g",
                "assignee": "sandcastle",
                "idempotency_key": "task_done:abc123:r2",
            }
        ],
    )
    procs: dict = {}

    wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: _SpawnHandle(proc),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 42.0,
    )

    assert set(procs) == {"t1"}
    assert procs["t1"].proc is proc
    assert procs["t1"].started_at == 42.0
    # #953 spawn context folded through for the terminal-boundary poll.
    assert procs["t1"].goal == "g"
    assert procs["t1"].idempotency_key == "task_done:abc123:r2"
    assert procs["t1"].spawned_at is not None
    # spawned_at MUST be tz-aware — the rework-shape evidence check compares it
    # against a tz-aware PR timestamp, and a naive value would raise.
    assert procs["t1"].spawned_at.tzinfo is not None


def test_tick_batch_shares_one_started_at_stamp():
    # #957 MAJOR (#1011): every proc drained in ONE tick shares the single
    # pre-drain stamp — the clock is sampled once, before the drain, NOT
    # per-task. This is deliberate (clock-first avoids orphaning just-spawned
    # handles if the clock raises mid-fold); the bounded skew across a batch is
    # negligible vs the multi-hour runaway threshold. Pin it so a future
    # "per-task accuracy" refactor can't silently regress the safety property
    # the TrackedProc docstring now documents.
    clock_calls: list = []

    def counting_clock() -> float:
        clock_calls.append(1)
        return 7.0

    tq = _RecordingTaskQueue(
        [],
        pending=[
            {"id": "t1", "goal": "g1", "assignee": "sandcastle"},
            {"id": "t2", "goal": "g2", "assignee": "sandcastle"},
        ],
    )
    procs: dict = {}

    wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=lambda goal, **_: _SpawnHandle(_TickProc(rc=None)),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=counting_clock,
    )

    assert set(procs) == {"t1", "t2"}
    # Both procs carry the same pre-drain stamp …
    assert procs["t1"].started_at == procs["t2"].started_at == 7.0
    # … because the clock was sampled exactly once for the whole batch.
    assert len(clock_calls) == 1


def test_tick_stamps_the_clock_before_spawning():
    # review #957-4 (MAJOR): the started_at stamp must be taken BEFORE the
    # drain runs. Taken after, a raising clock discards the just-spawned
    # handles — live children orphaned onto the 6h reaper. Clock-first means
    # a broken clock fails the step before any process exists.
    order: list = []

    def clock() -> float:
        order.append("clock")
        return 0.0

    def spawn(goal: str, **_: object) -> _SpawnHandle:
        order.append("spawn")
        return _SpawnHandle(_TickProc(rc=None))

    tq = _RecordingTaskQueue([], pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}])

    wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=spawn,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs={},
        task_clock=clock,
    )

    assert "spawn" in order and "clock" in order
    assert order.index("clock") < order.index("spawn")


def test_tick_with_a_broken_clock_spawns_nothing():
    # Consequence of clock-first ordering (review #957-4): a broken clock
    # fails Step 4 before the drain — no child is spawned only to have its
    # handle dropped on the floor.
    spawned: list = []

    def bad_clock() -> float:
        raise RuntimeError("clock broken")

    # Signature mirrors the production spawn call ``spawn(goal, task_id=...)``
    # (drain_tasks). With a bare ``def spawn(goal)`` a regression that reordered
    # the clock AFTER the drain would crash here with a TypeError on the missing
    # task_id kwarg — masking the real assertion (spawned == []) behind an
    # unrelated error (MEDIUM #1011 r3).
    def spawn(goal: str, **_: object) -> _SpawnHandle:
        spawned.append(goal)
        return _SpawnHandle(_TickProc(rc=None))

    tq = _RecordingTaskQueue([], pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}])

    wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_spawn=spawn,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs={},
        task_clock=bad_clock,
    )

    assert spawned == []


def test_tick_poll_blowup_does_not_block_the_runaway_killer():
    # review #957-6 (MINOR): Step 0's two halves are independent — a
    # completion-poll blowup must not stop the runaway killer from bounding
    # live processes. Each half needs its own isolation.
    class _ExplodingProc:
        pid = 1

        def poll(self) -> int | None:
            raise RuntimeError("handle gone")

    log: list = []
    tq = _RecordingTaskQueue(log)
    runaway = _TickProc(rc=None)
    procs = {
        "runaway": TrackedProc(proc=runaway, started_at=0.0),
        "exploder": TrackedProc(proc=_ExplodingProc(), started_at=0.0),
    }
    killed: list = []

    wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 999_999.0,
        task_running_reap_after_seconds=60,
        task_kill=killed.append,
    )

    assert killed == [runaway]


def test_tick_without_task_procs_reaps_all_stale_running_as_orphans():
    # AC7 restart simulation: no map (fresh driver / --once) → poll and kill
    # are skipped, and EVERY stale running row is an orphan again — reaped to
    # failed; Path-A re-drives the lost work as fresh events.
    log: list = []
    tq = _RecordingTaskQueue(log, stale_running=[{"id": "r1"}, {"id": "r2"}])

    result = wake_driver.tick(
        FakeEventQueue([]),
        wake_driver.default_orchestrator,
        stale_after_seconds=300,
        task_port=tq,
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
    )

    assert result.tasks_reaped == 2
    assert result.tasks_done == 0
    assert result.tasks_failed_exit == 0


def test_run_retains_the_tracking_map_across_ticks():
    # AC2 end-to-end: tick 1 spawns t1 into the injected map; the process
    # exits between ticks; tick 2 polls the SAME map and closes running→done.
    # A per-tick map would lose the handle and never close the row.
    log: list = []
    q = FakeEventQueue([])
    q.wake_signals = [True, True]
    tq = _RecordingTaskQueue(log, pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}])
    proc = _TickProc(rc=None)  # alive during tick 1...
    procs: dict = {}
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        if ticks["n"] == 2:
            proc._rc = 0  # ...exits before tick 2
        return ticks["n"] <= 2

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        should_continue=should_continue,
        task_port=tq,
        task_spawn=lambda goal, **_: _SpawnHandle(proc),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_procs=procs,
        task_clock=lambda: 0.0,
    )

    assert ("t1", "done", None) in tq.transitions
    assert procs == {}


def test_run_creates_and_retains_a_map_when_not_injected():
    # run() must own a map even when none is injected — otherwise the
    # production loop (main()) would never close running→done.
    log: list = []
    q = FakeEventQueue([])
    q.wake_signals = [True, True]
    tq = _RecordingTaskQueue(log, pending=[{"id": "t1", "goal": "g", "assignee": "sandcastle"}])
    proc = _TickProc(rc=None)
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        if ticks["n"] == 2:
            proc._rc = 0
        return ticks["n"] <= 2

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        should_continue=should_continue,
        task_port=tq,
        task_spawn=lambda goal, **_: _SpawnHandle(proc),
        task_resolve_binary=lambda: "claude",
        task_read_usage=_healthy_usage,
        task_clock=lambda: 0.0,
    )

    assert ("t1", "done", None) in tq.transitions


def test_run_forwards_task_read_usage_to_the_drain():
    # #921 AC4: without forwarding, the drain silently falls back to the
    # production probe (live Supabase) no matter what main() injected.
    calls = {"n": 0}

    def probe() -> _HealthyUsage:
        calls["n"] += 1
        return _HealthyUsage()

    q = FakeEventQueue([])
    q.wake_signals = [True]
    ticks = {"n": 0}

    def should_continue() -> bool:
        ticks["n"] += 1
        return ticks["n"] <= 1

    wake_driver.run(
        q,
        wake_driver.default_orchestrator,
        should_continue=should_continue,
        task_port=_RecordingTaskQueue([]),
        task_resolve_binary=lambda: "claude",
        task_read_usage=probe,
    )

    assert calls["n"] == 1


# --- #922: task_queue NOTIFY channel for cap-freed task dispatch --------


def test_task_queue_channel_constant_distinct_from_events():
    """AC1: the task_queue NOTIFY channel constant exists and is distinct
    from the events channel (cap-freed signals must not collide)."""
    assert wake_driver.TASK_QUEUE_CHANNEL == "task_queue"
    assert wake_driver.TASK_QUEUE_CHANNEL != wake_driver.EVENTS_CHANNEL


class _RecordingConn:
    """Minimal psycopg.Connection stand-in that records execute() SQL.

    PsycopgEventQueue.__init__ only calls conn.execute(...) + conn.commit();
    no live DB is needed to verify the LISTEN wiring it issues at construction.
    """

    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    def execute(self, sql, *args):
        self.executed.append(sql)
        return self

    def commit(self):
        self.commits += 1


def test_psycopg_event_queue_listens_on_both_channels_at_construction():
    """AC2: constructing PsycopgEventQueue issues LISTEN on BOTH the events
    channel and the task_queue channel, then commits — so a cap-freed NOTIFY
    actually wakes the loop. Behavioral check, not just constant existence."""
    conn = _RecordingConn()

    wake_driver.PsycopgEventQueue(conn)

    assert f"LISTEN {wake_driver.EVENTS_CHANNEL}" in conn.executed
    assert f"LISTEN {wake_driver.TASK_QUEUE_CHANNEL}" in conn.executed
    assert conn.commits >= 1


# --- main() resource management (#953 PR #1011 round 3) ---------------------


class _CloseRecordingClient:
    """Stand-in for the lifetime HttpxGitHubClient that records close() calls."""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _wire_main(monkeypatch, *, run_impl) -> _CloseRecordingClient:
    """Patch out main()'s heavy wiring; return the evidence client it builds.

    main() resolves ``default_github_client`` / ``get_client`` via
    function-local imports, so they are patched at their source modules (the
    local import rebinds the name from there at call time)."""
    client = _CloseRecordingClient()
    monkeypatch.setattr("sys.argv", ["wake_driver"])
    monkeypatch.setattr(wake_driver, "_configure_logging", lambda: None)
    monkeypatch.setattr(wake_driver, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(wake_driver, "_build_psycopg_queue", lambda **k: object())
    monkeypatch.setattr(wake_driver, "SupabaseTaskQueue", lambda *a, **k: object())
    monkeypatch.setattr(wake_driver, "_default_event_emit", lambda **k: (lambda *a, **kk: None))
    monkeypatch.setattr("agents.github_client.default_github_client", lambda: client)
    monkeypatch.setattr("agents.supabase_client.get_client", lambda: object())
    monkeypatch.setattr(wake_driver, "run", run_impl)
    return client


def test_main_closes_evidence_client_on_normal_exit(monkeypatch):
    # MEDIUM (#1011 r3): the driver holds ONE HttpxGitHubClient for its whole
    # lifetime; main() must release its pooled TCP/TLS connections when the loop
    # returns normally — otherwise a supervised restart loop leaks sockets.
    client = _wire_main(monkeypatch, run_impl=lambda *a, **k: None)

    assert wake_driver.main() == 0
    assert client.closed == 1


def test_main_closes_evidence_client_when_run_raises(monkeypatch):
    # The leak is worst exactly when the loop dies unexpectedly (a SIGTERM
    # handler raising, an unhandled error). The finally must fire on the
    # exception path too, then let the error propagate for crash-restart.
    def boom(*a, **k):
        raise RuntimeError("loop died")

    client = _wire_main(monkeypatch, run_impl=boom)

    with pytest.raises(RuntimeError, match="loop died"):
        wake_driver.main()
    assert client.closed == 1


def test_main_once_closes_evidence_client(monkeypatch):
    # L1 (#1029): the ``--once`` short-circuit builds the lifetime evidence
    # client just like the long-running path but returns BEFORE the outer
    # try/finally that closes it. Without its own finally the one-shot path
    # leaks the pooled sockets on every drain/watchdog invocation.
    client = _wire_main(monkeypatch, run_impl=lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["wake_driver", "--once"])

    tick_result = types.SimpleNamespace(
        reclaimed=0,
        processed=0,
        requeued=0,
        tasks_reclaimed=0,
        tasks_reaped=0,
        tasks_spawned=0,
        tasks_failed=0,
    )
    monkeypatch.setattr(wake_driver, "tick", lambda *a, **k: tick_result)

    assert wake_driver.main() == 0
    assert client.closed == 1


# --- AC-A (#1385): main() wires build_production_orchestrator, not the stub -


def test_main_wires_build_production_orchestrator_into_run(monkeypatch):
    # #1385 AC-A: the long-running loop must route through the real
    # orchestrator factory, not the mechanics-only default_orchestrator stub.
    sentinel = object()
    monkeypatch.setattr("agents.orchestrator.build_production_orchestrator", lambda **k: sentinel)
    captured: dict[str, object] = {}

    def _capture_run(queue, orchestrator, **kwargs):
        captured["orchestrator"] = orchestrator

    _wire_main(monkeypatch, run_impl=_capture_run)

    assert wake_driver.main() == 0
    assert captured["orchestrator"] is sentinel


def test_main_wires_telegram_notifier_into_build_production_orchestrator(monkeypatch):
    # #1385 AC-D: the live driver must pass agents.notify.telegram_notifier,
    # not leave the escalation path silently un-notified.
    captured_kwargs: dict[str, object] = {}

    def _capture_factory(**kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr("agents.orchestrator.build_production_orchestrator", _capture_factory)
    _wire_main(monkeypatch, run_impl=lambda *a, **k: None)

    assert wake_driver.main() == 0

    from agents.notify import telegram_notifier

    assert captured_kwargs.get("notifier") is telegram_notifier


def test_main_once_wires_build_production_orchestrator_into_tick(monkeypatch):
    # Same factory, one-shot path.
    sentinel = object()
    monkeypatch.setattr("agents.orchestrator.build_production_orchestrator", lambda **k: sentinel)
    captured: dict[str, object] = {}
    tick_result = types.SimpleNamespace(
        reclaimed=0,
        processed=0,
        requeued=0,
        tasks_reclaimed=0,
        tasks_reaped=0,
        tasks_spawned=0,
        tasks_failed=0,
    )

    def _capture_tick(queue, orchestrator, **kwargs):
        captured["orchestrator"] = orchestrator
        return tick_result

    _wire_main(monkeypatch, run_impl=lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["wake_driver", "--once"])
    monkeypatch.setattr(wake_driver, "tick", _capture_tick)

    assert wake_driver.main() == 0
    assert captured["orchestrator"] is sentinel


# --- AC-E (#1385): staged rollout — --dry-run / --no-task-drain -------------


def test_dry_run_maps_handle_event_over_events_with_no_side_effects():
    # Pure preview: handle_event alone (not dispatch), no queue writes.
    events = [_ev("a"), _ev("b")]
    seen: list[dict] = []
    sentinel_decisions = [object(), object()]

    def fake_handle_event(event):
        seen.append(event)
        return sentinel_decisions[len(seen) - 1]

    decisions = wake_driver.dry_run(events, fake_handle_event)

    assert decisions == sentinel_decisions
    assert seen == events
    # Events themselves are untouched — still "pending", never claimed.
    assert events[0]["state"] == "pending"
    assert events[1]["state"] == "pending"


def test_fake_event_queue_recent_events_is_read_only_and_limited():
    q = FakeEventQueue([_ev("a"), _ev("b"), _ev("c")])

    result = q.recent_events(limit=2)

    assert [e["id"] for e in result] == ["a", "b"]
    assert all(e["state"] == "pending" for e in q.events)  # no claim side effect


def _wire_main_dry_run(monkeypatch, *, recent_events, handle_event=None):
    """Wire main() for --dry-run: block every heavy/live-side-effect builder.

    If any of SupabaseTaskQueue / default_github_client / get_client /
    build_production_orchestrator is invoked, the test fails loudly — the
    dry-run preview must never construct them.
    """

    def _forbidden(name):
        def _raise(*a, **k):
            raise AssertionError(f"{name} must not be called under --dry-run")

        return _raise

    fake_queue = types.SimpleNamespace(recent_events=recent_events)
    monkeypatch.setattr("sys.argv", ["wake_driver", "--dry-run"])
    monkeypatch.setattr(wake_driver, "_configure_logging", lambda: None)
    monkeypatch.setattr(wake_driver, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(wake_driver, "_build_psycopg_queue", lambda: fake_queue)
    monkeypatch.setattr(wake_driver, "SupabaseTaskQueue", _forbidden("SupabaseTaskQueue"))
    monkeypatch.setattr(
        "agents.github_client.default_github_client", _forbidden("default_github_client")
    )
    monkeypatch.setattr("agents.supabase_client.get_client", _forbidden("get_client"))
    monkeypatch.setattr(
        "agents.orchestrator.build_production_orchestrator",
        _forbidden("build_production_orchestrator"),
    )
    monkeypatch.setattr(
        "agents.orchestrator.handle_event",
        handle_event or (lambda event: object()),
    )
    monkeypatch.setattr(wake_driver, "run", _forbidden("run"))
    monkeypatch.setattr(wake_driver, "tick", _forbidden("tick"))


def test_main_dry_run_previews_without_building_live_side_effects(monkeypatch, capsys):
    # Stage 1 of the #1385 AC-E rollout: read-only preview, no claim, no
    # SupabaseTaskQueue/evidence-client/orchestrator-factory construction.
    calls: dict[str, object] = {}

    def _recent_events(*, limit):
        calls["limit"] = limit
        return [{"id": "a", "event_type": "github.pr.opened", "severity": "info"}]

    _wire_main_dry_run(monkeypatch, recent_events=_recent_events)

    assert wake_driver.main() == 0
    assert calls["limit"] == 200  # default --dry-run-limit

    captured = capsys.readouterr()
    assert "github.pr.opened" in captured.out
    assert "1 events previewed, 0 would ESCALATE" in captured.out


def test_main_dry_run_limit_flag_is_threaded_through(monkeypatch):
    calls: dict[str, object] = {}

    def _recent_events(*, limit):
        calls["limit"] = limit
        return []

    _wire_main_dry_run(monkeypatch, recent_events=_recent_events)
    monkeypatch.setattr("sys.argv", ["wake_driver", "--dry-run", "--dry-run-limit", "50"])

    assert wake_driver.main() == 0
    assert calls["limit"] == 50


def test_main_no_task_drain_passes_none_task_port_into_run(monkeypatch):
    # Stage 2: task_port=None so tick's steps 0/2/4 (each already gated on
    # `task_port is not None`) skip — zero `claude -p` spawn — while routing
    # and escalation (independent of task_port) still run live.
    #
    # SupabaseTaskQueue IS still built under --no-task-drain (#1475 review,
    # MEDIUM): the poller's get_task_statuses lookup needs it regardless of
    # whether this driver instance drains/spawns tasks, and it's built once
    # off the shared client rather than skipped and later reconstructed
    # unshared — only task_port itself stays None.
    captured: dict[str, object] = {}

    def _capture_run(queue, orchestrator, **kwargs):
        captured["task_port"] = kwargs.get("task_port")

    client = _wire_main(monkeypatch, run_impl=_capture_run)
    monkeypatch.setattr("sys.argv", ["wake_driver", "--no-task-drain"])

    assert wake_driver.main() == 0
    assert captured["task_port"] is None
    assert client.closed == 1


def test_main_no_task_drain_still_wires_production_orchestrator(monkeypatch):
    # Routing/escalation must stay live under --no-task-drain — only task
    # spawn is suppressed, not the router or the notifier plumbing.
    sentinel = object()
    captured: dict[str, object] = {}

    def _capture_factory(**kwargs):
        return sentinel

    def _capture_run(queue, orchestrator, **kwargs):
        captured["orchestrator"] = orchestrator

    monkeypatch.setattr("agents.orchestrator.build_production_orchestrator", _capture_factory)
    _wire_main(monkeypatch, run_impl=_capture_run)
    monkeypatch.setattr("sys.argv", ["wake_driver", "--no-task-drain"])

    assert wake_driver.main() == 0
    assert captured["orchestrator"] is sentinel


def test_main_without_no_task_drain_still_builds_supabase_task_queue(monkeypatch):
    # Default (no flag): task_port stays a live SupabaseTaskQueue.
    sentinel_task_port = object()
    captured: dict[str, object] = {}

    def _capture_run(queue, orchestrator, **kwargs):
        captured["task_port"] = kwargs.get("task_port")

    client = _wire_main(monkeypatch, run_impl=_capture_run)
    monkeypatch.setattr(wake_driver, "SupabaseTaskQueue", lambda *a, **k: sentinel_task_port)

    assert wake_driver.main() == 0
    assert captured["task_port"] is sentinel_task_port
    assert client.closed == 1


# --- boot-time reliability: file logging + startup retry --------------------
#
# Incident: the AtLogOn Scheduled Task failed at boot 2026-08-06, exhausted
# its RestartCount=5/1-min budget within ~5 minutes with zero diagnosable
# output (Task Scheduler captures no stdout/stderr), and sat dead until a
# manual restart worked immediately — a boot-time network/DB-readiness race.


def _reset_root_logger():
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    return root, saved_handlers, saved_level


def _restore_root_logger(root, saved_handlers, saved_level):
    for h in root.handlers:
        h.close()
    root.handlers = saved_handlers
    root.setLevel(saved_level)


def test_configure_logging_adds_rotating_file_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(wake_driver, "_LOG_DIR", str(tmp_path))
    root, saved_handlers, saved_level = _reset_root_logger()
    try:
        wake_driver._configure_logging()
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(tmp_path / "wake_driver.log")
    finally:
        _restore_root_logger(root, saved_handlers, saved_level)


def test_configure_logging_falls_back_to_console_on_oserror(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wake_driver, "_LOG_DIR", str(tmp_path))

    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(wake_driver.os, "makedirs", _boom)
    root, saved_handlers, saved_level = _reset_root_logger()
    try:
        wake_driver._configure_logging()  # must not raise
        assert not any(isinstance(h, RotatingFileHandler) for h in root.handlers)
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        assert "file logging unavailable" in capsys.readouterr().err
    finally:
        _restore_root_logger(root, saved_handlers, saved_level)


def test_configure_logging_is_a_noop_when_already_configured(monkeypatch):
    # main() may run more than once in-process (tests, --once loops); a
    # second _configure_logging() must not open a second file handle.
    root, saved_handlers, saved_level = _reset_root_logger()
    marker = logging.StreamHandler()
    root.addHandler(marker)
    try:
        wake_driver._configure_logging()
        assert root.handlers == [marker]
    finally:
        _restore_root_logger(root, saved_handlers, saved_level)


def test_call_with_retry_returns_on_first_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr(wake_driver.time, "sleep", lambda s: sleeps.append(s))

    result = wake_driver._call_with_retry(lambda: "ok", what="thing")

    assert result == "ok"
    assert sleeps == []


def test_call_with_retry_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(wake_driver.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def _build():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("not ready yet")
        return "connected"

    result = wake_driver._call_with_retry(
        _build, what="thing", attempts=5, base_delay=1.0, max_delay=10.0
    )

    assert result == "connected"
    assert attempts["n"] == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff between the 2 failed attempts


def test_call_with_retry_raises_last_exception_after_exhausting_attempts(monkeypatch):
    sleeps = []
    monkeypatch.setattr(wake_driver.time, "sleep", lambda s: sleeps.append(s))

    def _build():
        raise ConnectionError("still not ready")

    with pytest.raises(ConnectionError, match="still not ready"):
        wake_driver._call_with_retry(
            _build, what="thing", attempts=3, base_delay=1.0, max_delay=10.0
        )

    assert len(sleeps) == 2  # attempts-1 sleeps, no sleep after the final failure


def test_call_with_retry_caps_delay_at_max_delay(monkeypatch):
    sleeps = []
    monkeypatch.setattr(wake_driver.time, "sleep", lambda s: sleeps.append(s))

    def _build():
        raise ConnectionError("nope")

    with pytest.raises(ConnectionError):
        wake_driver._call_with_retry(
            _build, what="thing", attempts=6, base_delay=5.0, max_delay=15.0
        )

    assert sleeps == [5.0, 10.0, 15.0, 15.0, 15.0]


def test_main_wraps_build_psycopg_queue_in_startup_retry(monkeypatch):
    # The transient boot-time DB/network failure must self-heal in-process
    # instead of relying solely on Task Scheduler's restart count.
    calls = {"n": 0}

    def _flaky_build_queue(**k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("boot-time DNS not ready")
        return object()

    client = _wire_main(monkeypatch, run_impl=lambda *a, **k: None)
    monkeypatch.setattr(wake_driver, "_build_psycopg_queue", _flaky_build_queue)
    monkeypatch.setattr(wake_driver.time, "sleep", lambda s: None)

    assert wake_driver.main() == 0
    assert calls["n"] == 2
    assert client.closed == 1
    assert client.closed == 1
