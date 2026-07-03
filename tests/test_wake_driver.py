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
import tomllib
from pathlib import Path

import pytest

from agents import wake_driver
from agents.task_dispatch import TaskQueuePort, TrackedProc

_REPO_ROOT = Path(__file__).resolve().parent.parent

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
    assert "time.sleep(" not in src, (
        "wake_driver must not busy-sleep; it blocks on the NOTIFY socket"
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
    monkeypatch.setattr(wake_driver, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(wake_driver, "_build_psycopg_queue", lambda: object())
    monkeypatch.setattr(wake_driver, "SupabaseTaskQueue", lambda *a, **k: object())
    monkeypatch.setattr(
        wake_driver, "_default_event_emit", lambda **k: (lambda *a, **kk: None)
    )
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
