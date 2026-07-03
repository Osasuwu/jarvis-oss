"""Unit tests for agents/task_dispatch.py — the #909 task-dispatch loop.

The whole point of AC10 is that the dispatch logic is a pure function over a
``TaskQueuePort`` Protocol plus an injected ``spawn`` callable, so every test
here runs against an in-memory fake — no Supabase client, no real ``claude``
binary. The one exception is the AC8 billing-trap test, which drives the *real*
``executor.spawn`` through the drain path (with an injected ``Popen``) to prove
the env-sanitization safety property survives integration.

Each test names the acceptance criterion it covers (see issue #909, grilled
2026-06-01, decision 2489782f).
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

from agents.task_dispatch import (
    DEFAULT_ASSIGNEE,
    DEFAULT_CONCURRENCY_CAP,
    DrainResult,
    SupabaseTaskQueue,
    TaskQueuePort,
    TrackedProc,
    drain_tasks,
    kill_process_tree,
    kill_runaways,
    poll_completions,
    reclaim_stale_tasks,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _row(task_id: str, *, assignee: str = "sandcastle", goal: str | None = None) -> dict[str, Any]:
    return {
        "id": task_id,
        "goal": goal or f"do {task_id}",
        "assignee": assignee,
        "status": "pending",
    }


class FakeTaskQueue:
    """In-memory ``TaskQueuePort`` for driving ``drain_tasks`` deterministically.

    ``claim_next`` hands out seeded pending rows FIFO, filtered by assignee —
    mirroring the real SELECT filter so a non-matching row is never claimed.
    """

    def __init__(
        self, *, pending: list[dict[str, Any]] | None = None, running_count: int = 0
    ) -> None:
        self._pending = list(pending or [])
        self._running_count = running_count
        self.claimed: list[str] = []
        self.transitions: list[tuple[str, str, str | None]] = []
        self.reclaimed_count = 0
        self.stale_running: list[dict[str, Any]] = []
        self.requeued: list[str] = []

    def claim_next(self, *, assignee: str) -> dict[str, Any] | None:
        for i, row in enumerate(self._pending):
            if row.get("assignee") == assignee:
                claimed = self._pending.pop(i)
                self.claimed.append(claimed["id"])
                return claimed
        return None

    def count_running(self, *, assignee: str) -> int:
        return self._running_count

    def transition(
        self, task_id: str, to_status: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        self.transitions.append((task_id, to_status, reason))
        return {"id": task_id, "status": to_status}

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        return self.reclaimed_count

    def list_stale_running(
        self, *, assignee: str, older_than_seconds: float
    ) -> list[dict[str, Any]]:
        return list(self.stale_running)

    def requeue_running(self, task_id: str) -> bool:
        self.requeued.append(task_id)
        return True


def _always_resolve() -> str:
    return "claude"


class _FakeProc:
    """Minimal ``Popen``-shaped handle: ``poll()`` returns the scripted rc."""

    def __init__(self, rc: int | None = None, pid: int = 4242) -> None:
        self._rc = rc
        self.pid = pid
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        return self._rc

    def kill(self) -> None:
        self.killed = True
        self._rc = -9

    def wait(self, timeout: float | None = None) -> int | None:
        self.waited = True
        return self._rc


class _AdoptedProc:
    """``psutil.Process``-shaped handle as folded in by boot adoption (#952).

    Deliberately exposes ONLY ``is_running()`` — NO ``poll()``, NO
    ``returncode`` — exactly like a real ``psutil.Process``. This is the handle
    kind that wedged ``running`` rows before the poll_exit fix: a bare
    ``.poll()`` in poll_completions/kill_runaways AttributeErrors on it.
    """

    def __init__(self, *, running: bool, pid: int = 4242) -> None:
        self._running = running
        self.pid = pid

    def is_running(self) -> bool:
        return self._running


class _ThrottledResult:
    """Stand-in for ``executor.SpawnResult`` when quota is near-exhaustion.

    No process was launched (``proc=None``); the ``throttled`` flag is the
    signal :func:`drain_tasks` must honor instead of counting a spawn.
    """

    proc = None
    throttled = True
    reason = "quota near-exhaustion"


class _FixedProbe:
    def __init__(self, reading: Any) -> None:
        self._reading = reading

    def read(self) -> Any:
        return self._reading


def _healthy_reading() -> Any:
    from agents.usage_probe import UsageReading

    return UsageReading(
        limit_window=timedelta(hours=5),
        used=10,
        total=100,
        reset_at=datetime.now(UTC),
        near_exhaustion=False,
    )


def _exhausted_reading() -> Any:
    from agents.usage_probe import UsageReading

    return UsageReading(
        limit_window=timedelta(hours=5),
        used=100,
        total=100,
        reset_at=datetime.now(UTC),
        near_exhaustion=True,
    )


def _healthy_usage() -> Any:
    """Injectable stand-in for ``read_usage`` — plenty of headroom."""
    return _healthy_reading()


# ---------------------------------------------------------------------------
# AC3 — concurrency cap: budget = cap − count_running, sampled once
# ---------------------------------------------------------------------------


class TestConcurrencyCap:
    def test_budget_limits_spawns(self) -> None:
        # 3 already running, cap 5 -> budget 2 -> only 2 of 4 pending spawned.
        q = FakeTaskQueue(pending=[_row(f"t{i}") for i in range(4)], running_count=3)
        spawns: list[str] = []
        res = drain_tasks(
            q,
            lambda g, task_id=None: spawns.append(g),  # AC3 #953 — spawn now accepts task_id
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert len(spawns) == 2
        assert len(q.claimed) == 2
        assert res.spawned == 2

    def test_no_spawn_when_at_cap(self) -> None:
        q = FakeTaskQueue(pending=[_row("t0")], running_count=5)
        spawns: list[str] = []
        res = drain_tasks(
            q,
            lambda g, task_id=None: spawns.append(g),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert spawns == []
        assert q.claimed == []
        assert res.spawned == 0

    def test_default_cap_is_five(self) -> None:
        assert DEFAULT_CONCURRENCY_CAP == 5


# ---------------------------------------------------------------------------
# AC2 — assignee routing: only 'sandcastle' claimed; 'owner' never spawned
# ---------------------------------------------------------------------------


class TestAssigneeRouting:
    def test_owner_rows_never_claimed(self) -> None:
        q = FakeTaskQueue(
            pending=[_row("own", assignee="owner"), _row("sand", assignee="sandcastle")],
            running_count=0,
        )
        spawns: list[str] = []
        drain_tasks(
            q, lambda g, task_id=None: spawns.append(g), resolve_binary=_always_resolve, read_usage=_healthy_usage
        )
        assert q.claimed == ["sand"]
        assert spawns == ["do sand"]

    def test_default_assignee_is_sandcastle(self) -> None:
        assert DEFAULT_ASSIGNEE == "sandcastle"


# ---------------------------------------------------------------------------
# AC4 — Ordering B: claim → transition(running) → spawn
# ---------------------------------------------------------------------------


class TestOrderingB:
    def test_transition_running_before_spawn(self) -> None:
        events: list[tuple[str, ...]] = []
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)

        original = q.transition

        def recording_transition(task_id: str, to_status: str, *, reason: str | None = None) -> Any:
            events.append(("transition", task_id, to_status))
            return original(task_id, to_status, reason=reason)

        q.transition = recording_transition  # type: ignore[method-assign]

        drain_tasks(
            q,
            lambda g, task_id=None: events.append(("spawn", g)),
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        # The running transition must be recorded BEFORE the spawn — a row is
        # never left 'claimed' once we have committed to spawning it, and spawn
        # is never invoked for a row still 'pending'/'claimed'.
        assert events == [("transition", "t0", "running"), ("spawn", "do t0")]


# ---------------------------------------------------------------------------
# AC9 — atomic claim: lost race returns None -> no spawn, no phantom work
# ---------------------------------------------------------------------------


class TestAtomicClaim:
    def test_empty_queue_no_spawn(self) -> None:
        q = FakeTaskQueue(pending=[], running_count=0)
        spawns: list[str] = []
        res = drain_tasks(
            q, lambda g, task_id=None: spawns.append(g), resolve_binary=_always_resolve, read_usage=_healthy_usage
        )
        assert spawns == []
        assert res.spawned == 0

    def test_none_claim_stops_drain_no_phantom_spawn(self) -> None:
        # Budget allows 5 but a competing drainer left only one claimable row;
        # subsequent claims return None and the loop stops cleanly without
        # spawning for an unclaimed row (the optimistic-lock atomicity that
        # makes the lost race safe is unit-tested in test_agents_task_queue).
        q = FakeTaskQueue(pending=[_row("only")], running_count=0)
        spawns: list[str] = []
        res = drain_tasks(
            q,
            lambda g, task_id=None: spawns.append(g),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert spawns == ["do only"]
        assert res.spawned == 1


# ---------------------------------------------------------------------------
# AC7a — binary unresolved: skip whole drain, zero claims, self-heals
# ---------------------------------------------------------------------------


class TestBinaryPreflight:
    def test_unresolved_binary_skips_entire_drain(self) -> None:
        q = FakeTaskQueue(pending=[_row("t0"), _row("t1")], running_count=0)
        spawns: list[str] = []

        def resolve_missing() -> str:
            raise FileNotFoundError("claude binary not found")

        res = drain_tasks(q, lambda g, task_id=None: spawns.append(g), resolve_binary=resolve_missing)

        assert q.claimed == []  # zero claims — no row touched
        assert spawns == []
        assert res.skipped_no_binary is True
        assert res.spawned == 0
        assert len(q._pending) == 2  # rows stay pending -> next drain self-heals

    def test_permissionerror_in_resolve_also_skips_drain(self) -> None:
        # A binary that exists but is not executable means "cannot spawn" just
        # as much as a missing one — skip the whole drain, claim nothing.
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        spawns: list[str] = []

        def resolve_denied() -> str:
            raise PermissionError("claude is not executable")

        res = drain_tasks(q, lambda g, task_id=None: spawns.append(g), resolve_binary=resolve_denied)

        assert res.skipped_no_binary is True
        assert q.claimed == []
        assert spawns == []

    def test_importerror_in_resolve_also_skips_drain(self) -> None:
        # A broken executor import surfaces as ImportError from the lazy
        # default resolver — still "cannot spawn", so skip not strand.
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)

        def resolve_broken() -> str:
            raise ImportError("executor dependency missing")

        res = drain_tasks(q, lambda g, task_id=None: None, resolve_binary=resolve_broken)

        assert res.skipped_no_binary is True
        assert q.claimed == []


# ---------------------------------------------------------------------------
# #921 AC4 — quota pre-flight: near-exhaustion skips the entire drain
# ---------------------------------------------------------------------------


class TestQuotaPreflight:
    def test_near_exhaustion_skips_entire_drain(self) -> None:
        # Zero claims, zero churn — rows stay visibly pending. The false-safe
        # probe contract means a broken probe also lands here.
        q = FakeTaskQueue(pending=[_row("t0"), _row("t1")], running_count=0)
        spawns: list[str] = []
        res = drain_tasks(
            q,
            lambda g, task_id=None: spawns.append(g),
            resolve_binary=_always_resolve,
            read_usage=_exhausted_reading,
        )
        assert q.claimed == []
        assert spawns == []
        assert res.throttled is True
        assert res.spawned == 0
        assert len(q._pending) == 2

    def test_consulted_once_at_drain_start(self) -> None:
        # One probe per drain, not one per task — mirrors the binary pre-flight.
        calls = {"n": 0}

        def usage() -> Any:
            calls["n"] += 1
            return _healthy_reading()

        q = FakeTaskQueue(pending=[_row(f"t{i}") for i in range(3)], running_count=0)
        res = drain_tasks(
            q, lambda g, task_id=None: None, cap=5, resolve_binary=_always_resolve, read_usage=usage
        )
        assert calls["n"] == 1
        assert res.spawned == 3


# ---------------------------------------------------------------------------
# AC7b — spawn raises: mark that task failed (terminal), continue the drain
# ---------------------------------------------------------------------------


class TestSpawnFailureIsTerminal:
    def test_spawn_raise_marks_failed_and_continues(self) -> None:
        q = FakeTaskQueue(pending=[_row("boom"), _row("ok")], running_count=0)
        spawns: list[str] = []

        def spawn(goal: str, task_id: str | None = None) -> None:
            if goal == "do boom":
                raise RuntimeError("spawn blew up")
            spawns.append(goal)

        res = drain_tasks(
            q, spawn, cap=5, resolve_binary=_always_resolve, read_usage=_healthy_usage
        )

        failed = [t for t in q.transitions if t[1] == "failed"]
        assert len(failed) == 1
        assert failed[0][0] == "boom"
        assert failed[0][2] and "spawn" in failed[0][2]  # reason documents the cause
        # 'boom' was transitioned running THEN failed (no retry); 'ok' spawned.
        assert ("boom", "running", None) in q.transitions
        assert spawns == ["do ok"]
        assert res.spawned == 1
        assert res.failed == 1

    def test_failed_mark_raise_does_not_abort_drain(self) -> None:
        # review #957-1 (MAJOR): the AC7b failure-marking itself can raise
        # (store outage). That must not escape drain_tasks — an escape discards
        # DrainResult.procs for already-spawned tasks, orphaning live children
        # onto the 6h reaper instead of next-tick completion polling.
        class Q(FakeTaskQueue):
            def transition(
                self, task_id: str, to_status: str, *, reason: str | None = None
            ) -> dict[str, Any]:
                if to_status == "failed":
                    raise RuntimeError("store outage")
                return super().transition(task_id, to_status, reason=reason)

        handle = object()
        q = Q(pending=[_row("boom"), _row("ok")], running_count=0)

        def spawn(goal: str, task_id: str | None = None) -> Any:
            if goal == "do boom":
                raise RuntimeError("spawn blew up")
            return _HealthySpawnResult(handle)

        res = drain_tasks(
            q, spawn, cap=5, resolve_binary=_always_resolve, read_usage=_healthy_usage
        )

        assert res.failed == 1  # still counted despite the failed-mark raising
        assert res.spawned == 1  # the drain continued past the bad row
        assert res.procs == (("ok", handle),)  # spawned handle survives for tracking


# ---------------------------------------------------------------------------
# review #2 — throttled spawn (quota near-exhaustion) is not a spawn
# ---------------------------------------------------------------------------


class TestThrottledSpawn:
    def test_throttle_stops_drain_without_miscounting(self) -> None:
        # Quota near-exhaustion: executor.spawn returns throttled=True, proc=None
        # — no process launched. The pre-fix bug counted this as `spawned` and
        # drained the WHOLE budget into 'running' rows, orphaning every one for
        # the 6h reaper. The drain must instead bail after a single row.
        q = FakeTaskQueue(pending=[_row(f"t{i}") for i in range(4)], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _ThrottledResult(),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        assert res.spawned == 0
        assert res.failed == 0
        assert res.throttled is True
        # Exactly one row was claimed+transitioned-running before the drain
        # bailed — bounded blast radius (that row is requeued, #921), not the cap.
        assert len(q.claimed) == 1
        assert ("t0", "running", None) in q.transitions
        # A throttle is NOT a spawn failure — the row must not be marked failed.
        assert [t for t in q.transitions if t[1] == "failed"] == []

    def test_throttle_midway_spawns_healthy_then_stops(self) -> None:
        # First task spawns healthily, the second hits the quota wall. The
        # healthy spawn still counts; the drain then stops on the throttle.
        calls = {"n": 0}

        def spawn(goal: str, task_id: str | None = None) -> Any:
            calls["n"] += 1
            return None if calls["n"] == 1 else _ThrottledResult()

        q = FakeTaskQueue(pending=[_row("t0"), _row("t1"), _row("t2")], running_count=0)
        res = drain_tasks(
            q, spawn, cap=5, resolve_binary=_always_resolve, read_usage=_healthy_usage
        )

        assert res.spawned == 1
        assert res.throttled is True
        assert len(q.claimed) == 2  # t0 (spawned) + t1 (throttled, requeued)

    def test_drain_result_has_throttled_field_default_false(self) -> None:
        assert DrainResult().throttled is False


# ---------------------------------------------------------------------------
# #921 AC4 — mid-drain throttle requeues the in-flight running row to pending
# ---------------------------------------------------------------------------


class TestThrottleRequeue:
    def test_midthrottle_requeues_running_row(self) -> None:
        # The throttled row is already `running` (Ordering B) but no process
        # exists — requeue it to `pending` instead of stranding it 6h for the
        # reaper to fail a task that never ran.
        q = FakeTaskQueue(pending=[_row("t0"), _row("t1")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _ThrottledResult(),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        assert res.throttled is True
        assert q.requeued == ["t0"]
        # Requeued ≠ failed — the row must not get a terminal transition.
        assert [t for t in q.transitions if t[1] == "failed"] == []

    def test_requeue_raise_leaves_row_for_reaper(self) -> None:
        # If the requeue UPDATE itself fails, the row stays `running` and the
        # AC5/AC6 reaper is the backstop — the drain must not crash.
        class Q(FakeTaskQueue):
            def requeue_running(self, task_id: str) -> bool:
                raise RuntimeError("supabase transient error")

        q = Q(pending=[_row("t0")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _ThrottledResult(),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        assert res.throttled is True
        assert [t for t in q.transitions if t[1] == "failed"] == []

    def test_requeue_false_is_tolerated(self) -> None:
        # Optimistic-lock miss (row changed under us) → False; same backstop.
        class Q(FakeTaskQueue):
            def requeue_running(self, task_id: str) -> bool:
                return False

        q = Q(pending=[_row("t0")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _ThrottledResult(),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        assert res.throttled is True

    def test_healthy_drain_never_requeues(self) -> None:
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        drain_tasks(
            q,
            lambda g, task_id=None: _HealthySpawnResult(object()),
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert q.requeued == []


# ---------------------------------------------------------------------------
# review #5 — a failed running-transition leaves the row claimed (AC5 reclaims)
# ---------------------------------------------------------------------------


class TestRunningTransitionFailureIsSafe:
    def test_transition_running_raise_skips_row_no_spawn(self) -> None:
        spawns: list[str] = []

        class Q(FakeTaskQueue):
            def transition(self, task_id: str, to_status: str, *, reason: str | None = None) -> Any:
                if to_status == "running":
                    raise RuntimeError("supabase transient error")
                return super().transition(task_id, to_status, reason=reason)

        q = Q(pending=[_row("t0")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: spawns.append(g),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        # The row was claimed but the running transition failed: never spawned,
        # never marked failed — left 'claimed' for the AC5 reclaimer.
        assert spawns == []
        assert res.spawned == 0
        assert res.failed == 0
        assert [t for t in q.transitions if t[1] == "failed"] == []

    def test_transition_running_raise_continues_to_next_row(self) -> None:
        # A transient transition error on one row must not abort the drain —
        # it skips that row (left claimed) and continues with the next.
        spawns: list[str] = []

        class Q(FakeTaskQueue):
            def transition(self, task_id: str, to_status: str, *, reason: str | None = None) -> Any:
                if to_status == "running" and task_id == "bad":
                    raise RuntimeError("transient")
                return super().transition(task_id, to_status, reason=reason)

        q = Q(pending=[_row("bad"), _row("good")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: spawns.append(g),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )

        assert spawns == ["do good"]
        assert res.spawned == 1


# ---------------------------------------------------------------------------
# AC8 — billing-trap holds through the integrated drain path
# ---------------------------------------------------------------------------


class _CapturedPopen:
    """Records argv + env passed to each Popen instantiation (no real process)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})

        class _Handle:
            pid = 4242

            def poll(self) -> None:
                return None

        return _Handle()


class TestBillingTrapThroughDrain:
    def test_api_keys_stripped_from_spawned_env(self, monkeypatch: Any, tmp_path: Any) -> None:
        from agents import executor

        fake_bin = tmp_path / "claude.exe"
        fake_bin.write_text("")

        captured = _CapturedPopen()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-be-stripped")
        monkeypatch.setenv("CLAUDE_API_KEY", "sk-also-stripped")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-stripped")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://metered.example/v1")
        monkeypatch.setenv("CLAUDE_BASE_URL", "https://metered.example/v1")
        monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake_bin))
        monkeypatch.setenv("PATH_FROM_PARENT", "keep-me")

        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)

        def spawn(goal: str, task_id: str | None = None) -> Any:
            return executor.spawn(
                goal,
                popen=captured,
                probe=_FixedProbe(_healthy_reading()),
                stderr_log_dir=str(tmp_path),
            )

        drain_tasks(q, spawn, resolve_binary=lambda: str(fake_bin), read_usage=_healthy_usage)

        assert len(captured.calls) == 1
        env = captured.calls[0]["env"]
        assert "ANTHROPIC_API_KEY" not in env, "billing-trap leak through drain path"
        assert "CLAUDE_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" not in env, "base-url redirect leak through drain path"
        assert "CLAUDE_BASE_URL" not in env
        assert env.get("PATH_FROM_PARENT") == "keep-me", "non-sensitive env must survive"


# ---------------------------------------------------------------------------
# AC5 — reclaim_stale_tasks: stale claimed -> pending (assignee+threshold scoped)
# ---------------------------------------------------------------------------


class TestReclaimStaleClaimed:
    def test_reclaims_with_assignee_and_threshold(self) -> None:
        calls: dict[str, Any] = {}

        class Q(FakeTaskQueue):
            def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
                calls["claimed"] = (assignee, older_than_seconds)
                return 1

            def list_stale_running(self, *, assignee: str, older_than_seconds: float) -> list:
                calls["running"] = (assignee, older_than_seconds)
                return []

        res = reclaim_stale_tasks(
            Q(),
            assignee="sandcastle",
            claimed_stale_after_seconds=300,
            running_reap_after_seconds=21600,
        )
        assert calls["claimed"] == ("sandcastle", 300)
        assert calls["running"] == ("sandcastle", 21600)
        assert res.reclaimed_claimed == 1

    def test_reclaimed_count_propagates(self) -> None:
        q = FakeTaskQueue()
        q.reclaimed_count = 3
        res = reclaim_stale_tasks(q)
        assert res.reclaimed_claimed == 3


# ---------------------------------------------------------------------------
# AC6 — running reaper: stale running -> failed; nothing stale -> no-op
# ---------------------------------------------------------------------------


class TestRunningReaper:
    def test_stale_running_marked_failed(self) -> None:
        q = FakeTaskQueue()
        q.stale_running = [{"id": "stuck1"}, {"id": "stuck2"}]
        res = reclaim_stale_tasks(q, running_reap_after_seconds=21600)
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert {t[0] for t in failed} == {"stuck1", "stuck2"}
        assert all(t[2] and "reaped" in t[2] for t in failed)
        assert res.reaped_running == 2

    def test_noop_when_nothing_stale(self) -> None:
        q = FakeTaskQueue()
        q.stale_running = []
        res = reclaim_stale_tasks(q)
        assert res.reaped_running == 0
        assert q.transitions == []


# ---------------------------------------------------------------------------
# #921 AC5 — reaper is orphan-only: live-tracked rows are never time-reaped
# ---------------------------------------------------------------------------


class TestOrphanOnlyReaper:
    def test_live_rows_not_reaped(self) -> None:
        # A row with a live tracked process is NOT an orphan, however old —
        # long-running tasks are legitimate; runaways are AC6's job (tree-kill),
        # not the time-reaper's.
        q = FakeTaskQueue()
        q.stale_running = [{"id": "live"}, {"id": "orphan"}]
        res = reclaim_stale_tasks(q, live_task_ids={"live"})
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert {t[0] for t in failed} == {"orphan"}
        assert res.reaped_running == 1

    def test_orphan_reason_names_orphan(self) -> None:
        q = FakeTaskQueue()
        q.stale_running = [{"id": "orphan"}]
        reclaim_stale_tasks(q, live_task_ids=set())
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert failed[0][2] and "orphan" in failed[0][2]

    def test_default_live_set_empty_reaps_all_stale(self) -> None:
        # Restart semantics: a fresh driver has no map, so every stale running
        # row is an orphan (AC7 — the in-memory map does not survive restart).
        q = FakeTaskQueue()
        q.stale_running = [{"id": "a"}, {"id": "b"}]
        res = reclaim_stale_tasks(q)
        assert res.reaped_running == 2

    def test_orphan_transition_raise_isolated(self) -> None:
        # review #957-5 (MINOR): one orphan's failed-mark raising must not
        # abort the sweep — the remaining orphans are still reaped this pass
        # (per-row isolation, mirroring poll_completions).
        class Q(FakeTaskQueue):
            def transition(
                self, task_id: str, to_status: str, *, reason: str | None = None
            ) -> dict[str, Any]:
                if task_id == "bad":
                    raise RuntimeError("transient store error")
                return super().transition(task_id, to_status, reason=reason)

        q = Q()
        q.stale_running = [{"id": "bad"}, {"id": "ok"}]
        res = reclaim_stale_tasks(q)
        assert res.reaped_running == 1  # 'bad' raised → not counted
        failed = [t[0] for t in q.transitions if t[1] == "failed"]
        assert failed == ["ok"]  # the sweep continued past the bad row


# ---------------------------------------------------------------------------
# #921 AC1 — DrainResult exposes spawned (task_id, proc) pairs
# ---------------------------------------------------------------------------


class _HealthySpawnResult:
    """Stand-in for ``executor.SpawnResult`` on the healthy path: proc launched."""

    def __init__(self, proc: Any) -> None:
        self.proc = proc
        self.throttled = False
        self.reason = None


class TestSpawnedProcPairs:
    def test_healthy_spawn_contributes_pair(self) -> None:
        handle = object()
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _HealthySpawnResult(handle),
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert res.procs == (("t0", handle),)
        assert res.spawned == 1

    def test_multiple_healthy_spawns_pair_in_claim_order(self) -> None:
        handles = {"do t0": object(), "do t1": object()}
        q = FakeTaskQueue(pending=[_row("t0"), _row("t1")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _HealthySpawnResult(handles[g]),
            cap=5,
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert res.procs == (("t0", handles["do t0"]), ("t1", handles["do t1"]))

    def test_throttled_spawn_contributes_no_pair(self) -> None:
        # No process launched — nothing for the driver to poll.
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        res = drain_tasks(
            q,
            lambda g, task_id=None: _ThrottledResult(),
            resolve_binary=_always_resolve,
            read_usage=_healthy_usage,
        )
        assert res.procs == ()

    def test_raising_spawn_contributes_no_pair(self) -> None:
        def spawn(goal: str, task_id: str | None = None) -> Any:
            raise RuntimeError("spawn blew up")

        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        res = drain_tasks(q, spawn, resolve_binary=_always_resolve, read_usage=_healthy_usage)
        assert res.procs == ()
        assert res.failed == 1

    def test_procless_spawn_result_contributes_no_pair(self) -> None:
        # A spawn returning None (or result.proc=None) launched nothing the
        # driver can poll — counted as spawned, but no pair.
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        res = drain_tasks(
            q, lambda g, task_id=None: None, resolve_binary=_always_resolve, read_usage=_healthy_usage
        )
        assert res.procs == ()
        assert res.spawned == 1

    def test_procs_default_empty_tuple(self) -> None:
        assert DrainResult().procs == ()


# ---------------------------------------------------------------------------
# #921 AC2 — poll_completions closes running→done/failed on process exit
# ---------------------------------------------------------------------------


class TestPollCompletions:
    def test_still_running_kept_no_transition(self) -> None:
        q = FakeTaskQueue()
        procs = {"t0": TrackedProc(_FakeProc(rc=None), started_at=0.0)}
        res = poll_completions(q, procs)
        assert res.done == 0
        assert res.failed_exit == 0
        assert "t0" in procs
        assert q.transitions == []

    def test_exit_zero_closes_done_and_drops(self) -> None:
        # Model P: done = the spawned process exited 0 — NOT task success,
        # NOT PR merged. Path-A events carry the actual outcome.
        q = FakeTaskQueue()
        procs = {"t0": TrackedProc(_FakeProc(rc=0), started_at=0.0)}
        res = poll_completions(q, procs)
        assert res.done == 1
        assert ("t0", "done", None) in q.transitions
        assert procs == {}

    def test_nonzero_exit_closes_failed_with_rc_in_reason(self) -> None:
        q = FakeTaskQueue()
        procs = {"t0": TrackedProc(_FakeProc(rc=3), started_at=0.0)}
        res = poll_completions(q, procs)
        assert res.failed_exit == 1
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert len(failed) == 1
        assert failed[0][0] == "t0"
        assert failed[0][2] and "exit 3" in failed[0][2]
        assert procs == {}

    def test_mixed_batch(self) -> None:
        q = FakeTaskQueue()
        procs = {
            "live": TrackedProc(_FakeProc(rc=None), started_at=0.0),
            "ok": TrackedProc(_FakeProc(rc=0), started_at=0.0),
            "boom": TrackedProc(_FakeProc(rc=1), started_at=0.0),
        }
        res = poll_completions(q, procs)
        assert res.done == 1
        assert res.failed_exit == 1
        assert set(procs) == {"live"}

    def test_transition_raise_isolated_drops_entry(self) -> None:
        # One bad row must not block the others. The bad entry is dropped —
        # its row stays `running` and the reaper backstop fails it later.
        class Q(FakeTaskQueue):
            def transition(self, task_id: str, to_status: str, *, reason: str | None = None) -> Any:
                if task_id == "bad":
                    raise RuntimeError("supabase transient error")
                return super().transition(task_id, to_status, reason=reason)

        q = Q()
        procs = {
            "bad": TrackedProc(_FakeProc(rc=0), started_at=0.0),
            "ok": TrackedProc(_FakeProc(rc=0), started_at=0.0),
        }
        res = poll_completions(q, procs)
        assert res.done == 1  # only 'ok' counted
        assert ("ok", "done", None) in q.transitions
        assert procs == {}  # both dropped — 'bad' falls to the reaper backstop


class TestPollCompletionsAdoptedProcess:
    """#952 regression — adopted psutil.Process handles (no poll()) must flow
    through poll_completions, not AttributeError-wedge the row in ``running``.

    Before the poll_exit fix, boot adoption folded raw psutil.Process handles
    into ``procs``; ``tracked.proc.poll()`` raised AttributeError (swallowed by
    the driver's tick guard), so adopted rows NEVER completed, were NEVER
    orphan-reaped (still in live_task_ids), and leaked a cap slot forever —
    strictly worse than the pre-sidecar empty-map self-heal.
    """

    def test_exited_adopted_proc_closes_failed_unknown_exit(self) -> None:
        # AC5: the true exit code of a process we did not spawn is unknowable,
        # so an exited adopted process closes ``failed`` (never ``done``).
        q = FakeTaskQueue()
        procs = {"t0": TrackedProc(_AdoptedProc(running=False), started_at=0.0)}
        res = poll_completions(q, procs)
        assert res.done == 0
        assert res.failed_exit == 1
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert len(failed) == 1 and failed[0][0] == "t0"
        assert procs == {}

    def test_running_adopted_proc_kept_no_transition(self) -> None:
        q = FakeTaskQueue()
        procs = {"t0": TrackedProc(_AdoptedProc(running=True), started_at=0.0)}
        res = poll_completions(q, procs)
        assert res.done == 0 and res.failed_exit == 0
        assert "t0" in procs
        assert q.transitions == []

    def test_runaway_adopted_proc_is_tree_killed(self) -> None:
        # kill_runaways must also tolerate the adopted handle — a still-running
        # adopted process past max runtime is killed, not skipped on a raise.
        q = FakeTaskQueue()
        kills: list[Any] = []
        procs = {"t0": TrackedProc(_AdoptedProc(running=True), started_at=0.0)}
        n = kill_runaways(q, procs, max_runtime_seconds=100, now=lambda: 200.0, kill=kills.append)
        assert n == 1
        assert len(kills) == 1
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert failed and failed[0][0] == "t0"
        assert procs == {}


# ---------------------------------------------------------------------------
# #921 AC6 — runaway live processes are tree-killed and failed
# ---------------------------------------------------------------------------


class TestKillRunaways:
    def test_runaway_killed_failed_and_dropped(self) -> None:
        q = FakeTaskQueue()
        proc = _FakeProc(rc=None)
        kills: list[Any] = []
        procs = {"t0": TrackedProc(proc, started_at=0.0)}
        n = kill_runaways(q, procs, max_runtime_seconds=100, now=lambda: 200.0, kill=kills.append)
        assert n == 1
        assert kills == [proc]
        failed = [t for t in q.transitions if t[1] == "failed"]
        assert failed[0][0] == "t0"
        assert failed[0][2] and "killed" in failed[0][2] and "max runtime" in failed[0][2]
        assert procs == {}

    def test_young_live_proc_untouched(self) -> None:
        q = FakeTaskQueue()
        kills: list[Any] = []
        procs = {"t0": TrackedProc(_FakeProc(rc=None), started_at=0.0)}
        n = kill_runaways(q, procs, max_runtime_seconds=100, now=lambda: 50.0, kill=kills.append)
        assert n == 0
        assert kills == []
        assert "t0" in procs
        assert q.transitions == []

    def test_exited_proc_skipped_for_poll(self) -> None:
        # An already-exited proc is poll_completions' job (rc decides
        # done/failed); killing it would be a no-op and failing it could lie.
        q = FakeTaskQueue()
        kills: list[Any] = []
        procs = {"t0": TrackedProc(_FakeProc(rc=0), started_at=0.0)}
        n = kill_runaways(q, procs, max_runtime_seconds=100, now=lambda: 200.0, kill=kills.append)
        assert n == 0
        assert kills == []
        assert "t0" in procs

    def test_runaway_kill_emits_task_failed(self) -> None:
        # AC1 (#953): a runaway-reaped task is the worst-case stuck class the
        # reconciliation exists to catch — it MUST emit task_failed so the
        # orchestrator can re-drive/escalate. poll_completions emits at its
        # terminal boundary but kill_runaways did not (MAJOR, PR #1011 round-4).
        q = FakeTaskQueue()
        emitted: list[dict[str, Any]] = []

        def emit(
            event_type: str, severity: str, payload: dict[str, Any], *, dedup_key: str | None = None
        ) -> None:
            emitted.append(
                {
                    "event_type": event_type,
                    "severity": severity,
                    "payload": payload,
                    "dedup_key": dedup_key,
                }
            )

        procs = {
            "t0": TrackedProc(
                _FakeProc(rc=None),
                started_at=0.0,
                goal="implement #5",
                idempotency_key="task_done:lin5:r2",
            )
        }
        n = kill_runaways(
            q,
            procs,
            max_runtime_seconds=100,
            now=lambda: 200.0,
            kill=lambda _p: None,
            event_emit=emit,
        )
        assert n == 1
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["event_type"] == "task_failed"
        assert ev["payload"]["exit_confirmed"] is True
        assert ev["payload"]["pr_evidence"] is None
        assert "killed" in ev["payload"]["failure_reason"]
        # lineage/attempt parsed from the idempotency key (re-drive keying).
        assert ev["payload"]["lineage_key"] == "task_done:lin5"
        assert ev["payload"]["attempt"] == 2
        assert ev["dedup_key"] == "task_failed:t0:a2"

    def test_runaway_kill_emit_failure_does_not_block_transition(self) -> None:
        # Decoupled emit (MAJOR, PR #1011): a raising event_emit must NOT stop
        # the FSM transition — else the killed row wedges in `running` until the
        # 6h reaper, exactly the stall poll_completions' decoupling avoids.
        q = FakeTaskQueue()

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("supabase down")

        procs = {"t0": TrackedProc(_FakeProc(rc=None), started_at=0.0)}
        n = kill_runaways(
            q,
            procs,
            max_runtime_seconds=100,
            now=lambda: 200.0,
            kill=lambda _p: None,
            event_emit=boom,
        )
        assert n == 1
        assert {t[0] for t in q.transitions if t[1] == "failed"} == {"t0"}
        assert procs == {}

    def test_runaway_kill_no_emit_when_emitter_absent(self) -> None:
        # With no event_emit wired the #921 behavior is unchanged: kill + fail,
        # no events (the dispatch loop runs eventless until wake_driver wires it).
        q = FakeTaskQueue()
        procs = {"t0": TrackedProc(_FakeProc(rc=None), started_at=0.0)}
        n = kill_runaways(q, procs, max_runtime_seconds=100, now=lambda: 200.0, kill=lambda _p: None)
        assert n == 1
        assert procs == {}

    def test_kill_raise_isolated_keeps_entry(self) -> None:
        # A failed kill leaves a possibly-alive process — do NOT fail the row
        # (that would lie); keep the entry and retry next tick.
        q = FakeTaskQueue()
        live = _FakeProc(rc=None)
        procs = {
            "bad": TrackedProc(live, started_at=0.0),
            "ok": TrackedProc(_FakeProc(rc=None), started_at=0.0),
        }

        calls = {"n": 0}

        def kill(proc: Any) -> None:
            calls["n"] += 1
            if proc is live:
                raise OSError("taskkill unavailable")

        n = kill_runaways(q, procs, max_runtime_seconds=100, now=lambda: 200.0, kill=kill)
        assert n == 1  # only 'ok' was killed+failed
        assert "bad" in procs and "ok" not in procs
        assert {t[0] for t in q.transitions if t[1] == "failed"} == {"ok"}
        assert calls["n"] == 2  # both attempted — isolation, not abort


class TestKillProcessTree:
    def test_windows_uses_taskkill_tree_force(self, monkeypatch: Any) -> None:
        # Bare Popen.kill() is terminate() on Windows — children survive. The
        # tree-kill must go through taskkill /T /F.
        import agents.task_dispatch as td

        runs: list[list[str]] = []
        monkeypatch.setattr(td.subprocess, "run", lambda argv, **kw: runs.append(list(argv)))
        proc = _FakeProc(rc=None, pid=1234)
        kill_process_tree(proc, platform="win32")
        assert runs == [["taskkill", "/PID", "1234", "/T", "/F"]]
        assert proc.killed is False  # win32 path never calls Popen.kill()

    def test_posix_uses_proc_kill(self) -> None:
        proc = _FakeProc(rc=None, pid=1234)
        kill_process_tree(proc, platform="linux")
        assert proc.killed is True

    def test_reaps_handle_with_wait(self, monkeypatch: Any) -> None:
        # review #957-3: the post-kill wait must actually run (it reaps the
        # handle so poll() reflects the death immediately), win32 and POSIX
        # alike — a fake without wait() was silently masking this call.
        import agents.task_dispatch as td

        monkeypatch.setattr(td.subprocess, "run", lambda argv, **kw: None)
        win = _FakeProc(rc=None, pid=1234)
        kill_process_tree(win, platform="win32")
        posix = _FakeProc(rc=None, pid=1234)
        kill_process_tree(posix, platform="linux")
        assert win.waited is True
        assert posix.waited is True

    def test_hung_wait_is_swallowed(self) -> None:
        # A child that won't reap within the timeout must not hang the driver
        # tick: TimeoutExpired is swallowed, the next tick's poll re-checks.
        class _HangingProc(_FakeProc):
            def wait(self, timeout: float | None = None) -> int | None:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout or 10)

        proc = _HangingProc(rc=None, pid=1234)
        kill_process_tree(proc, platform="linux")
        assert proc.killed is True  # the kill happened despite the hung wait

    def test_missing_taskkill_falls_back_to_proc_kill(self, monkeypatch: Any) -> None:
        # review #957-7 (MINOR): taskkill absent from PATH (stripped env,
        # minimal container) must not leave the runaway alive — fall back to
        # plain Popen.kill(), which at least takes down the direct child.
        import agents.task_dispatch as td

        def raising_run(argv: list[str], **kw: Any) -> None:
            raise FileNotFoundError("taskkill not found")

        monkeypatch.setattr(td.subprocess, "run", raising_run)
        proc = _FakeProc(rc=None, pid=1234)
        kill_process_tree(proc, platform="win32")
        assert proc.killed is True


# ---------------------------------------------------------------------------
# AC10 — injectable-port architecture
# ---------------------------------------------------------------------------


class TestInjectablePortArchitecture:
    def test_port_protocol_and_real_adapter_exist(self) -> None:
        # The Protocol is the seam; SupabaseTaskQueue is the prod adapter.
        adapter = SupabaseTaskQueue()  # constructible without touching the network
        assert isinstance(adapter, TaskQueuePort)  # runtime_checkable Protocol

    def test_drain_runs_against_a_fake_no_live_client(self) -> None:
        # If drain_tasks required a live client this whole module would not run.
        q = FakeTaskQueue(pending=[_row("t0")], running_count=0)
        spawns: list[str] = []
        drain_tasks(
            q, lambda g, task_id=None: spawns.append(g), resolve_binary=_always_resolve, read_usage=_healthy_usage
        )
        assert spawns == ["do t0"]
        assert os.environ is not None  # sanity — no monkeypatch leaked
