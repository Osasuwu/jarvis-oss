"""MVP integration tracer: ci_failure event -> sandcastle spawn (whole loop) (#746).

This is a **thin integration tracer**, not a unit test. It drives one real
``wake_driver.tick`` and asserts on the **actual** ``executor.spawn`` invocation
the production drain reaches — NOT a hand-called spawn under a mock (that
tautology killed the first attempt, closed PR #907). The spawn is only ever
reached if the whole chain fires:

    inject ci_failure event
      → wake_driver.tick / drain_pending (Step 3)          # event drain
      → orchestrator.handle_event  (pure Decision)          # routing
      → orchestrator.dispatch      (side effect)            # task_queue row
      → wake_driver.tick / drain_tasks (Step 4)             # task drain, #909
      → executor.spawn(claude -p) in sandcastle             # the spawn

Loop closure is EXTERNAL (GitHub workflows Path A: open → CI → review →
automerge → rework → escalate). Nothing here re-drives after the spawn; the
loop re-enters only via a fresh ``event``.

Demo (AC4) — reproduce the whole loop from a shell::

    python -c "from tests.test_ci_failure_integration import _demo; _demo()"

``_demo()`` injects a ci_failure event, drives one tick with a recording spawn,
and prints the spawn it observed (goal + task_id). Swap the recording spawn for
``agents.executor.spawn`` and a resolvable ``claude`` binary to watch a real
sandcastle ``claude -p`` attempt.

Acceptance criteria:
1. An injected ci_failure event drives, end-to-end through ``wake_driver.tick``,
   to an ``executor.spawn`` of ``claude -p`` in sandcastle (integration test,
   fixed routing, no real model). Asserted on the real spawn the drain reaches.
2. The spawned run inherits no API-billing keys (billing-trap holds in the
   integrated executor path).
3. After spawn the internal system does nothing further — a second tick spawns
   nothing new; loop re-entry is via a fresh event, not an internal pr_pipeline.
4. Documented demo (module docstring + ``_demo``): inject ci_failure → observe
   the sandcastle spawn attempt.
5. No internal module duplicates the GitHub-side Path A (automerge / rework-cap
   / escalate): the emitted goal carries none of those verbs, and exactly one
   task_queue row is written for the one event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents import executor, orchestrator, wake_driver

# Fixed clock — dispatch's escalation policy is weekday/weekend-aware; ci_failure
# routes to EMIT_TASK (not ESCALATE) so ``now`` is inert here, but it must be a
# real tz-aware datetime for the signature.
_FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

# A stale-after threshold so large that neither watchdog reclaims/reaps anything
# during the trace — we want the plain forward path, no crash-recovery noise.
_BIG = 10_000_000.0


# --- FSM-faithful fakes ----------------------------------------------------


class _FakeEventQueue:
    """In-memory model of the #739 events FSM, behind ``EventQueuePort``."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events: list[dict[str, Any]] = events or []
        self.clock: float = 0.0
        self.processed: list[str] = []
        self._severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

    def claim_next(self) -> dict[str, Any] | None:
        pending = [e for e in self.events if e["state"] == "pending"]
        if not pending:
            return None
        pending.sort(key=lambda e: (self._severity_rank.get(e.get("severity", "info"), 4), e["id"]))
        row = pending[0]
        row["state"] = "claimed"
        row["claimed_at"] = self.clock
        return dict(row)

    def mark_processed(self, event_id: str, *, action: str = "") -> bool:
        for e in self.events:
            if e["id"] == event_id and e["state"] == "claimed":
                e["state"] = "processed"
                self.processed.append(event_id)
                return True
        return False

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        count = 0
        for e in self.events:
            if (
                e["state"] == "claimed"
                and (self.clock - e.get("claimed_at", 0)) >= older_than_seconds
            ):
                e["state"] = "pending"
                e["claimed_at"] = None
                count += 1
        return count

    def wait_for_wake(self, *, timeout_seconds: float | None) -> bool:
        return False


def _ev(
    event_id: str,
    *,
    event_type: str = "ci_failure",
    severity: str = "high",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "event_type": event_type,
        "severity": severity,
        "payload": payload or {},
        "state": "pending",
        "claimed_at": None,
    }


class _FakeStore:
    """Single in-memory task store backing BOTH sides of the chain.

    - The **enqueue** side goes through ``task_queue.enqueue(..., client=self)``,
      which drives the supabase-client surface: ``.table(name).insert(row).execute()``.
    - The **drain** side is ``drain_tasks``/watchdogs calling the ``TaskQueuePort``
      methods (``claim_next`` / ``count_running`` / ``transition`` / ...).

    Both must see the SAME rows so a row enqueued in Step 3 (event drain) is
    claimable by Step 4 (task drain) within the same tick — that shared identity
    is exactly what makes this an integration trace and not two mocks talking
    past each other.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.transitions: list[tuple[str, str, str | None]] = []

    # -- supabase-client surface (enqueue path) --

    def table(self, name: str) -> _FakeStore:
        assert name == "task_queue", f"unexpected table {name!r}"
        return self

    def insert(self, payload: dict[str, Any]) -> _FakeStore:
        self._pending_insert = payload
        return self

    def execute(self) -> SimpleNamespace:
        payload = self._pending_insert
        key = payload.get("idempotency_key")
        if any(r.get("idempotency_key") == key for r in self.rows):
            return SimpleNamespace(data=[])  # idempotency collision — silent no-op
        stored = {**payload, "id": f"tq-{len(self.rows) + 1}"}
        self.rows.append(stored)
        return SimpleNamespace(data=[stored])

    # -- TaskQueuePort surface (drain path) --

    def claim_next(self, *, assignee: str) -> dict[str, Any] | None:
        for row in self.rows:
            if row["status"] == "pending" and row.get("assignee") == assignee:
                row["status"] = "claimed"
                return dict(row)
        return None

    def count_running(self, *, assignee: str) -> int:
        return sum(
            1 for r in self.rows if r["status"] == "running" and r.get("assignee") == assignee
        )

    def transition(
        self, task_id: str, to_status: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        self.transitions.append((task_id, to_status, reason))
        for row in self.rows:
            if row["id"] == task_id:
                row["status"] = to_status
                return dict(row)
        raise RuntimeError(f"task not found: {task_id}")

    def reclaim_stale_claimed(self, *, assignee: str, older_than_seconds: float) -> int:
        return 0

    def list_stale_running(
        self, *, assignee: str, older_than_seconds: float
    ) -> list[dict[str, Any]]:
        return []

    def requeue_running(self, task_id: str) -> bool:
        for row in self.rows:
            if row["id"] == task_id and row["status"] == "running":
                row["status"] = "pending"
                return True
        return False


class _DummyProc:
    """Minimal ``Popen``-shaped handle a recording spawn can hand back."""

    pid = 4242

    def poll(self) -> int | None:
        return None


def _make_recording_spawn(sink: list[dict[str, Any]]):
    """A drain-compatible spawn that records the goal + task_id it was handed.

    Matches the production ``Spawn`` contract (``spawn(goal, *, task_id=None)``
    → object with ``.proc`` / ``.throttled``) so ``drain_tasks`` treats it as a
    real launch. It records the arguments the PRODUCTION drain passed — this is
    the non-tautological evidence: the goal string here was built by
    ``handle_event`` and carried through ``dispatch`` → ``enqueue`` → ``claim_next``.
    """

    def _spawn(goal: str, *, task_id: str | None = None) -> executor.SpawnResult:
        sink.append({"goal": goal, "task_id": task_id})
        return executor.SpawnResult(proc=_DummyProc(), throttled=False)

    return _spawn


def _orchestrator_adapter(store: _FakeStore):
    """The sanctioned 'fixed routing' harness: real ``handle_event`` + real
    ``dispatch``, writing to the shared store.

    Production ``wake_driver.default_orchestrator`` is still a logging stub (the
    handle_event→dispatch wiring lands in a later #44 slice), so the tracer
    supplies the adapter. Both halves are production code — only the injection
    point is test-local."""

    def _run(event: dict[str, Any]) -> None:
        decision = orchestrator.handle_event(event)
        orchestrator.dispatch(decision, now=_FIXED_NOW, client=store)

    return _run


def _drive_one_tick(event_q: _FakeEventQueue, store: _FakeStore, spawn) -> wake_driver.TickResult:
    """Drive exactly one production tick with the tracer's injected surfaces."""
    return wake_driver.tick(
        event_q,
        _orchestrator_adapter(store),
        stale_after_seconds=_BIG,
        task_port=store,
        task_spawn=spawn,
        task_resolve_binary=lambda: "/fake/claude",
        task_read_usage=lambda: SimpleNamespace(near_exhaustion=False),
        task_procs=None,  # skip Step 0 completion poll — single forward trace
    )


# --- AC1: ci_failure event drives to executor.spawn through the whole loop ---


def test_ci_failure_event_drives_to_spawn_through_tick() -> None:
    """AC1: one injected ci_failure event → one real spawn, via wake_driver.tick.

    The spawn is asserted on the goal the PRODUCTION chain produced ("fix:
    ci_failure on 42"), reached through drain_tasks — not hand-called."""
    event_q = _FakeEventQueue([_ev("ci-1", payload={"pr": "42"})])
    store = _FakeStore()
    spawns: list[dict[str, Any]] = []

    result = _drive_one_tick(event_q, store, _make_recording_spawn(spawns))

    # The event was drained and the task was spawned in the SAME tick.
    assert result.processed == 1
    assert result.tasks_spawned == 1

    # The spawn the production drain reached carries the routed goal + task id.
    assert len(spawns) == 1
    assert spawns[0]["goal"] == "fix: ci_failure on 42"
    assert spawns[0]["task_id"] == "tq-1"

    # Exactly one sandcastle task_queue row was written for the one event.
    assert len(store.rows) == 1
    assert store.rows[0]["assignee"] == "sandcastle"


# --- AC2: billing-trap holds in the integrated executor path ---------------


def test_spawn_inherits_no_api_keys(tmp_path) -> None:
    """AC2: the spawned subprocess inherits no API-billing keys.

    Ported to the current ``executor.spawn`` (injectable ``popen=``, patched
    ``read_usage`` so the quota gate does not consult a real probe)."""
    captured_env: dict[str, str] | None = None

    class _CapturingPopen:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            nonlocal captured_env
            captured_env = kwargs.get("env", {})
            self.pid = 99999

    parent_env = {
        "PATH": "/usr/bin",
        "ANTHROPIC_API_KEY": "sk-leak-sentinel",
        "ANTHROPIC_AUTH_TOKEN": "token-leak",
        "CLAUDE_API_KEY": "sk-claude-leak",
        "ANTHROPIC_BASE_URL": "https://metered.example",
        "CLAUDE_BASE_URL": "https://metered.example",
        "KEEP_THIS": "safe-env-var",
    }
    healthy = SimpleNamespace(near_exhaustion=False, used=1, total=100)

    with (
        patch("agents.executor.os.environ", parent_env),
        patch("agents.executor.read_usage", return_value=healthy),
        patch("agents.executor._resolve_claude_binary", return_value="/fake/claude"),
    ):
        result = executor.spawn(
            "fix: ci_failure on 42",
            stderr_log_dir=str(tmp_path),
            popen=_CapturingPopen,
        )

    assert result.proc is not None
    assert result.throttled is False
    assert captured_env is not None
    # Every sensitive key stripped; the safe one survives.
    for leaked in executor._SENSITIVE_ENV_KEYS:
        assert leaked not in captured_env, f"{leaked} leaked into the spawned env"
    assert captured_env.get("KEEP_THIS") == "safe-env-var"


# --- AC3: after spawn the internal system does nothing further -------------


def test_second_tick_is_quiescent() -> None:
    """AC3: a second tick spawns nothing new — loop re-entry needs a fresh event."""
    event_q = _FakeEventQueue([_ev("ci-1", payload={"pr": "42"})])
    store = _FakeStore()
    spawns: list[dict[str, Any]] = []
    spawn = _make_recording_spawn(spawns)

    _drive_one_tick(event_q, store, spawn)
    assert len(spawns) == 1  # tick 1 spawned once
    assert event_q.processed == ["ci-1"]  # event consumed

    second = _drive_one_tick(event_q, store, spawn)

    # No pending event, the task row is already running — nothing re-drives.
    assert second.processed == 0
    assert second.tasks_spawned == 0
    assert len(spawns) == 1  # still one — no internal pr_pipeline
    assert len(store.rows) == 1


# --- AC3/AC5: routing emits a task, never spawns internally, no Path A dup ---


def test_orchestrator_emits_task_and_does_not_duplicate_path_a() -> None:
    """AC3 + AC5: handle_event routes to EMIT_TASK (no direct spawn); the goal
    carries no automerge/rework/escalate verb (Path A is GitHub-owned)."""
    decision = orchestrator.handle_event(_ev("ci-1", payload={"pr": "42"}))

    assert decision.route == orchestrator.Route.EMIT_TASK
    assert decision.assignee == "sandcastle"

    goal = decision.goal.lower()
    for path_a_verb in ("automerge", "auto-merge", "rework", "escalate", "merge"):
        assert path_a_verb not in goal, f"goal duplicates Path A verb: {path_a_verb!r}"

    # Dispatch writes exactly one row and returns without launching anything.
    store = _FakeStore()
    dispatched = orchestrator.dispatch(decision, now=_FIXED_NOW, client=store)
    assert dispatched.enqueued is True
    assert len(store.rows) == 1


# --- AC4: runnable demo -----------------------------------------------------


def _demo() -> dict[str, Any]:
    """Inject a ci_failure event, drive one tick, return the observed spawn.

    Documented entry point for the AC4 demo (see module docstring)."""
    event_q = _FakeEventQueue([_ev("ci-1", payload={"pr": "42"})])
    store = _FakeStore()
    spawns: list[dict[str, Any]] = []
    result = _drive_one_tick(event_q, store, _make_recording_spawn(spawns))
    observed = spawns[0] if spawns else {}
    print(  # noqa: T201 — demo entry point, intentional stdout
        f"[demo] ci_failure -> spawned={result.tasks_spawned} "
        f"goal={observed.get('goal')!r} task_id={observed.get('task_id')!r}"
    )
    return observed


if __name__ == "__main__":  # pragma: no cover — manual demo hook
    _demo()
