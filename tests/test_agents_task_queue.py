"""Tests for agents/task_queue.py — enqueue, claim_next, transition.

Uses a stub Supabase client. No live DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agents.task_queue import (
    _TERMINAL_STATES,
    _VALID_TRANSITIONS,
    claim_next,
    count_running,
    enqueue,
    list_active,
    list_stale_running,
    reclaim_stale_claimed,
    requeue_running,
    transition,
)


# ---------------------------------------------------------------------------
# Stub — minimal Supabase client emulation for task_queue operations
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class _StubSelect:
    def __init__(self, parent: _StubTable, rows: list[dict[str, Any]]) -> None:
        self._parent = parent
        self._rows = list(rows)
        self._filters: list[tuple[str, Any]] = []
        self._lt_filters: list[tuple[str, Any]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit_n: int | None = None

    def eq(self, col: str, val: Any) -> _StubSelect:
        self._filters.append((col, val))
        return self

    def lt(self, col: str, val: Any) -> _StubSelect:
        self._lt_filters.append((col, val))
        return self

    def order(self, col: str, *, desc: bool = False, **kwargs: Any) -> _StubSelect:
        self._order.append((col, desc))
        return self

    def limit(self, n: int) -> _StubSelect:
        self._limit_n = n
        return self

    def execute(self) -> _StubResponse:
        rows = list(self._rows)
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        for col, val in self._lt_filters:
            rows = [r for r in rows if r.get(col) is not None and r.get(col) < val]
        for col, desc in reversed(self._order):
            rows.sort(
                key=lambda r: (r.get(col) is None, r.get(col)),
                reverse=desc,
            )
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        self._parent.calls.append(("select", dict(self._filters)))
        return _StubResponse(data=rows)


class _StubUpdate:
    def __init__(
        self, parent: _StubTable, rows: list[dict[str, Any]], payload: dict[str, Any]
    ) -> None:
        self._parent = parent
        self._rows = rows
        self._payload = payload
        self._eq_filters: list[tuple[str, Any]] = []
        self._lt_filters: list[tuple[str, Any]] = []

    def eq(self, col: str, val: Any) -> _StubUpdate:
        self._eq_filters.append((col, val))
        return self

    def lt(self, col: str, val: Any) -> _StubUpdate:
        self._lt_filters.append((col, val))
        return self

    def execute(self) -> _StubResponse:
        matched: list[dict[str, Any]] = []
        for row in self._rows:
            eq_ok = all(row.get(c) == v for c, v in self._eq_filters)
            lt_ok = all(row.get(c) is not None and row.get(c) < v for c, v in self._lt_filters)
            if eq_ok and lt_ok:
                row.update(self._payload)
                matched.append(dict(row))
        self._parent.calls.append(("update", dict(self._eq_filters)))
        return _StubResponse(data=matched)


class _StubInsert:
    def __init__(
        self, parent: _StubTable, rows: list[dict[str, Any]], payload: dict[str, Any]
    ) -> None:
        self._parent = parent
        self._rows = rows
        self._payload = payload

    def execute(self) -> _StubResponse:
        for existing in self._rows:
            if existing.get("idempotency_key") == self._payload.get("idempotency_key"):
                return _StubResponse(data=[])
        stored = {
            **self._payload,
            "id": f"tq-{len(self._rows) + 1}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._rows.append(stored)
        self._parent.calls.append(("insert", self._payload.get("idempotency_key", "")))
        return _StubResponse(data=[stored])


class _StubTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[Any] = []

    def select(self, *args: Any, **kwargs: Any) -> _StubSelect:
        return _StubSelect(self, self._rows)

    def insert(self, payload: dict[str, Any]) -> _StubInsert:
        return _StubInsert(self, self._rows, payload)

    def update(self, payload: dict[str, Any]) -> _StubUpdate:
        return _StubUpdate(self, self._rows, payload)


class _StubClient:
    def __init__(self) -> None:
        self._tables: dict[str, _StubTable] = {}

    def table(self, name: str) -> _StubTable:
        if name not in self._tables:
            self._tables[name] = _StubTable([])
        return self._tables[name]

    def seed(self, table: str, rows: list[dict[str, Any]]) -> None:
        self.table(table)._rows.extend(rows)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> _StubClient:
    return _StubClient()


def _pending(**kw: Any) -> dict[str, Any]:
    base = {
        "id": "tq-pending",
        "goal": "test task",
        "priority": 0,
        "status": "pending",
        "claimed_at": None,
        "completed_at": None,
        "escalated_reason": None,
        "idempotency_key": "key-pending",
        "scope_files": [],
        "created_at": "2026-05-21T00:00:00",
        "updated_at": "2026-05-21T00:00:00",
    }
    base.update(kw)
    return base


def _claimed(**kw: Any) -> dict[str, Any]:
    base = _pending(
        id="tq-claimed",
        status="claimed",
        idempotency_key="key-claimed",
        claimed_at="2026-05-21T01:00:00",
    )
    base.update(kw)
    return base


def _running(**kw: Any) -> dict[str, Any]:
    base = _claimed(
        id="tq-running",
        status="running",
        idempotency_key="key-running",
        claimed_at="2026-05-21T01:00:00",
    )
    base.update(kw)
    return base


# ===========================================================================
# enqueue
# ===========================================================================


class TestEnqueue:
    def test_inserts_row(self, client: _StubClient) -> None:
        row = enqueue(
            goal="test task",
            priority=5,
            idempotency_key="key-1",
            client=client,
        )
        assert row is not None
        assert row["goal"] == "test task"
        assert row["priority"] == 5
        assert row["status"] == "pending"
        assert row["idempotency_key"] == "key-1"

    def test_with_assignee_and_scope(self, client: _StubClient) -> None:
        row = enqueue(
            goal="scoped task",
            priority=3,
            assignee="worker-a",
            idempotency_key="key-2",
            scope_files=["src/main.py"],
            client=client,
        )
        assert row is not None
        assert row["assignee"] == "worker-a"
        assert row["scope_files"] == ["src/main.py"]

    def test_idempotency_key_collision(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _pending(idempotency_key="dup-key"),
            ],
        )
        row = enqueue(
            goal="duplicate",
            priority=0,
            idempotency_key="dup-key",
            client=client,
        )
        assert row is None

    def test_default_priority_zero(self, client: _StubClient) -> None:
        row = enqueue(
            goal="default priority",
            idempotency_key="key-default",
            client=client,
        )
        assert row is not None
        assert row["priority"] == 0

    def test_escalated_reason_persisted_on_insert(self, client: _StubClient) -> None:
        row = enqueue(
            goal="owner escalation",
            priority=14,
            assignee="owner",
            idempotency_key="key-escalate",
            escalated_reason="security_alert (critical) — owner review required",
            client=client,
        )
        assert row is not None
        assert row["assignee"] == "owner"
        assert row["escalated_reason"] == ("security_alert (critical) — owner review required")

    def test_escalated_reason_omitted_when_none(self, client: _StubClient) -> None:
        row = enqueue(
            goal="no reason",
            idempotency_key="key-noreason",
            client=client,
        )
        assert row is not None
        assert "escalated_reason" not in row


# ===========================================================================
# claim_next
# ===========================================================================


class TestClaimNext:
    def test_claims_highest_priority(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _pending(id="low", priority=1, idempotency_key="key-low"),
                _pending(id="high", priority=10, idempotency_key="key-high"),
                _pending(id="medium", priority=5, idempotency_key="key-med"),
            ],
        )
        row = claim_next(client=client)
        assert row is not None
        assert row["id"] == "high"
        assert row["status"] == "claimed"
        assert row["claimed_at"] is not None

    def test_fifo_for_tie(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _pending(
                    id="first",
                    priority=5,
                    idempotency_key="key-a",
                    created_at="2026-05-21T00:00:00",
                ),
                _pending(
                    id="second",
                    priority=5,
                    idempotency_key="key-b",
                    created_at="2026-05-21T01:00:00",
                ),
            ],
        )
        row = claim_next(client=client)
        assert row is not None
        assert row["id"] == "first"

    def test_returns_none_when_empty(self, client: _StubClient) -> None:
        row = claim_next(client=client)
        assert row is None

    def test_ignores_non_pending_status(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _claimed(id="claimed-task"),
                _running(id="running-task"),
            ],
        )
        row = claim_next(client=client)
        assert row is None

    def test_optimistic_lock_race(self, client: _StubClient) -> None:
        """Another worker claimed the task between our read and update."""
        client.seed(
            "task_queue",
            [
                _pending(id="race", idempotency_key="key-race"),
            ],
        )

        # First claim succeeds
        first = claim_next(client=client)
        assert first is not None
        assert first["id"] == "race"

        # Second claim on same task fails (already claimed)
        second = claim_next(client=client)
        assert second is None


# ===========================================================================
# transition
# ===========================================================================


class TestTransition:
    def test_pending_to_claimed(self, client: _StubClient) -> None:
        client.seed("task_queue", [_pending()])
        row = transition("tq-pending", "claimed", client=client)
        assert row["status"] == "claimed"
        assert row["claimed_at"] is not None

    def test_claimed_to_running(self, client: _StubClient) -> None:
        client.seed("task_queue", [_claimed()])
        row = transition("tq-claimed", "running", client=client)
        assert row["status"] == "running"

    def test_running_to_done(self, client: _StubClient) -> None:
        client.seed("task_queue", [_running()])
        row = transition("tq-running", "done", client=client)
        assert row["status"] == "done"
        assert row["completed_at"] is not None

    def test_running_to_failed(self, client: _StubClient) -> None:
        client.seed("task_queue", [_running()])
        row = transition("tq-running", "failed", reason="timeout", client=client)
        assert row["status"] == "failed"
        assert row["completed_at"] is not None
        assert row["escalated_reason"] == "timeout"

    def test_running_to_parked(self, client: _StubClient) -> None:
        client.seed("task_queue", [_running()])
        row = transition("tq-running", "parked", client=client)
        assert row["status"] == "parked"

    def test_full_lifecycle(self, client: _StubClient) -> None:
        """pending → claimed → running → done"""
        client.seed("task_queue", [_pending(id="lifecycle")])

        r1 = transition("lifecycle", "claimed", client=client)
        assert r1["status"] == "claimed"

        r2 = transition("lifecycle", "running", client=client)
        assert r2["status"] == "running"

        r3 = transition("lifecycle", "done", client=client)
        assert r3["status"] == "done"

    # -- Error cases -------------------------------------------------------

    def test_illegal_transition(self, client: _StubClient) -> None:
        client.seed("task_queue", [_pending()])
        with pytest.raises(ValueError, match="Illegal transition"):
            transition("tq-pending", "done", client=client)

    def test_illegal_transition_from_claimed(self, client: _StubClient) -> None:
        client.seed("task_queue", [_claimed()])
        with pytest.raises(ValueError, match="Illegal transition"):
            transition("tq-claimed", "done", client=client)

    def test_transition_from_terminal_state(self, client: _StubClient) -> None:
        for terminal in ("done", "failed", "parked"):
            rows = [_running(id=f"tq-{terminal}", status=terminal)]
            c = _StubClient()
            c.seed("task_queue", rows)
            with pytest.raises(ValueError, match="terminal state"):
                transition(f"tq-{terminal}", "claimed", client=c)

    def test_task_not_found(self, client: _StubClient) -> None:
        with pytest.raises(RuntimeError, match="Task not found"):
            transition("nonexistent-id", "claimed", client=client)

    def test_uses_optimistic_lock(self, client: _StubClient) -> None:
        """Update always includes both id and status for race safety."""
        client.seed("task_queue", [_pending()])
        transition("tq-pending", "claimed", client=client)
        tq_calls = client.table("task_queue").calls
        update_calls = [c for c in tq_calls if c[0] == "update"]
        assert len(update_calls) >= 1
        filters = update_calls[0][1]
        assert "id" in filters
        assert "status" in filters

    def test_claimed_updated_by_another_worker(self, client: _StubClient) -> None:
        """Transition from a task already moved by another worker fails."""
        client.seed("task_queue", [_claimed()])
        transition("tq-claimed", "running", client=client)
        # A second call sees "running" -> "running" is not in FSM
        with pytest.raises(ValueError, match="Illegal transition"):
            transition("tq-claimed", "running", client=client)


# ===========================================================================
# FSM table integrity
# ===========================================================================


class TestFSMDefinition:
    """Validate the FSM transition table covers every non-terminal state."""

    _ALL_STATES = {
        "pending",
        "claimed",
        "running",
        "done",
        "failed",
        "parked",
        "skipped_duplicate",
    }

    def test_all_non_terminal_states_defined(self) -> None:
        all_states = self._ALL_STATES
        non_terminal = all_states - _TERMINAL_STATES
        defined = set(_VALID_TRANSITIONS.keys())
        assert defined == non_terminal, f"Missing transition rules for: {non_terminal - defined}"

    def test_no_transition_from_terminal_states(self) -> None:
        for state in _TERMINAL_STATES:
            assert state not in _VALID_TRANSITIONS

    def test_no_self_loops(self) -> None:
        for state, targets in _VALID_TRANSITIONS.items():
            assert state not in targets, f"Self-loop in {state}"

    def test_all_targets_are_valid_states(self) -> None:
        all_states = self._ALL_STATES
        for state, targets in _VALID_TRANSITIONS.items():
            for target in targets:
                assert target in all_states, (
                    f"Transition {state} -> {target}: {target!r} is not a valid state"
                )


# ===========================================================================
# claim_next — assignee filter (#909 AC2)
# ===========================================================================


class TestClaimNextAssignee:
    """AC2: drain claims/spawns ONLY assignee='sandcastle' rows; owner never."""

    def test_claims_only_matching_assignee(self, client: _StubClient) -> None:
        # owner row has higher priority — if the filter were post-claim instead
        # of in the SELECT, the owner row would be read first and the claim of a
        # sandcastle row would never happen.
        client.seed(
            "task_queue",
            [
                _pending(id="sand", assignee="sandcastle", priority=5, idempotency_key="k-s"),
                _pending(id="own", assignee="owner", priority=10, idempotency_key="k-o"),
            ],
        )
        row = claim_next(assignee="sandcastle", client=client)
        assert row is not None
        assert row["id"] == "sand"
        assert row["status"] == "claimed"

    def test_returns_none_when_no_matching_assignee(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _pending(id="own", assignee="owner", idempotency_key="k-o"),
            ],
        )
        assert claim_next(assignee="sandcastle", client=client) is None

    def test_no_assignee_filter_claims_any(self, client: _StubClient) -> None:
        # Backward-compat: omitting assignee preserves the original behavior.
        client.seed(
            "task_queue",
            [
                _pending(id="any", assignee="owner", idempotency_key="k"),
            ],
        )
        row = claim_next(client=client)
        assert row is not None
        assert row["id"] == "any"


# ===========================================================================
# count_running (#909 AC3)
# ===========================================================================


class TestCountRunning:
    """AC3: concurrency budget = cap − count_running(assignee)."""

    def test_counts_running_for_assignee(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _running(id="r1", assignee="sandcastle", idempotency_key="k1"),
                _running(id="r2", assignee="sandcastle", idempotency_key="k2"),
                _running(id="r3", assignee="owner", idempotency_key="k3"),
                _claimed(id="c1", assignee="sandcastle", idempotency_key="k4"),
                _pending(id="p1", assignee="sandcastle", idempotency_key="k5"),
            ],
        )
        assert count_running(assignee="sandcastle", client=client) == 2

    def test_zero_when_none_running(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _pending(id="p", assignee="sandcastle", idempotency_key="k"),
            ],
        )
        assert count_running(assignee="sandcastle", client=client) == 0


# ===========================================================================
# reclaim_stale_claimed (#909 AC5) — direct UPDATE bypassing the FSM
# ===========================================================================


class TestReclaimStaleClaimed:
    """AC5: stale `claimed` sandcastle rows return to `pending` (not-yet-spawned)."""

    _OLD = "2020-01-01T00:00:00+00:00"

    def test_reclaims_old_claimed_to_pending(self, client: _StubClient) -> None:
        fresh = datetime.now(timezone.utc).isoformat()
        client.seed(
            "task_queue",
            [
                _claimed(
                    id="stale", assignee="sandcastle", idempotency_key="k1", claimed_at=self._OLD
                ),
                _claimed(id="fresh", assignee="sandcastle", idempotency_key="k2", claimed_at=fresh),
            ],
        )
        n = reclaim_stale_claimed(assignee="sandcastle", older_than_seconds=300, client=client)
        assert n == 1
        rows = {r["id"]: r for r in client.table("task_queue")._rows}
        assert rows["stale"]["status"] == "pending"
        assert rows["stale"]["claimed_at"] is None
        assert rows["fresh"]["status"] == "claimed"

    def test_only_reclaims_matching_assignee(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _claimed(id="own", assignee="owner", idempotency_key="k1", claimed_at=self._OLD),
            ],
        )
        assert (
            reclaim_stale_claimed(assignee="sandcastle", older_than_seconds=300, client=client) == 0
        )

    def test_does_not_touch_running(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _running(
                    id="run", assignee="sandcastle", idempotency_key="k1", claimed_at=self._OLD
                ),
            ],
        )
        assert (
            reclaim_stale_claimed(assignee="sandcastle", older_than_seconds=300, client=client) == 0
        )
        rows = {r["id"]: r for r in client.table("task_queue")._rows}
        assert rows["run"]["status"] == "running"


# ===========================================================================
# list_stale_running (#909 AC6) — running-reaper candidate selection
# ===========================================================================


class TestListStaleRunning:
    """AC6: select stale `running` sandcastle rows for the reaper to fail."""

    _OLD = "2020-01-01T00:00:00+00:00"

    def test_lists_old_running_for_assignee(self, client: _StubClient) -> None:
        fresh = datetime.now(timezone.utc).isoformat()
        client.seed(
            "task_queue",
            [
                _running(
                    id="old", assignee="sandcastle", idempotency_key="k1", claimed_at=self._OLD
                ),
                _running(id="new", assignee="sandcastle", idempotency_key="k2", claimed_at=fresh),
                _running(id="own", assignee="owner", idempotency_key="k3", claimed_at=self._OLD),
                _claimed(
                    id="clm", assignee="sandcastle", idempotency_key="k4", claimed_at=self._OLD
                ),
            ],
        )
        rows = list_stale_running(assignee="sandcastle", older_than_seconds=300, client=client)
        assert {r["id"] for r in rows} == {"old"}


# ===========================================================================
# requeue_running (#921 AC4) — mid-drain throttle returns the row to pending
# ===========================================================================


class TestRequeueRunning:
    """AC4: a throttled-but-already-running row goes back to `pending` (no process)."""

    def test_requeues_running_to_pending(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _running(id="run", assignee="sandcastle", idempotency_key="k1"),
            ],
        )
        assert requeue_running("run", client=client) is True
        rows = {r["id"]: r for r in client.table("task_queue")._rows}
        assert rows["run"]["status"] == "pending"
        assert rows["run"]["claimed_at"] is None

    def test_returns_false_when_not_running(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _claimed(id="clm", idempotency_key="k1"),
            ],
        )
        assert requeue_running("clm", client=client) is False
        rows = {r["id"]: r for r in client.table("task_queue")._rows}
        assert rows["clm"]["status"] == "claimed"

    def test_returns_false_when_missing(self, client: _StubClient) -> None:
        assert requeue_running("ghost", client=client) is False


# ===========================================================================
# skipped_duplicate (#931) — dispatch-dedup terminal state
# ===========================================================================


class TestSkippedDuplicate:
    """#931: a running row whose issue already has a live PR terminates
    as `skipped_duplicate` — no requeue, no retry."""

    def test_running_to_skipped_duplicate(self, client: _StubClient) -> None:
        client.seed("task_queue", [_running()])
        row = transition(
            "tq-running",
            "skipped_duplicate",
            reason="open PR #555 closes #931",
            client=client,
        )
        assert row["status"] == "skipped_duplicate"
        assert row["completed_at"] is not None
        assert row["escalated_reason"] == "open PR #555 closes #931"

    def test_skipped_duplicate_is_terminal(self, client: _StubClient) -> None:
        assert "skipped_duplicate" in _TERMINAL_STATES
        client.seed("task_queue", [_running(id="tq-skip", status="skipped_duplicate")])
        with pytest.raises(ValueError, match="terminal state"):
            transition("tq-skip", "claimed", client=client)

    def test_pending_cannot_skip_directly(self, client: _StubClient) -> None:
        client.seed("task_queue", [_pending()])
        with pytest.raises(ValueError, match="Illegal transition"):
            transition("tq-pending", "skipped_duplicate", client=client)

    def test_claimed_cannot_skip_directly(self, client: _StubClient) -> None:
        client.seed("task_queue", [_claimed()])
        with pytest.raises(ValueError, match="Illegal transition"):
            transition("tq-claimed", "skipped_duplicate", client=client)


# ===========================================================================
# list_active (#931) — live claimed/running rows for sibling dedup
# ===========================================================================


class TestListActive:
    def test_lists_claimed_and_running_only(self, client: _StubClient) -> None:
        client.seed(
            "task_queue",
            [
                _pending(id="p", idempotency_key="k1"),
                _claimed(id="c", idempotency_key="k2"),
                _running(id="r", idempotency_key="k3"),
                _running(id="d", status="done", idempotency_key="k4"),
                _running(id="s", status="skipped_duplicate", idempotency_key="k5"),
            ],
        )
        rows = list_active(client=client)
        assert {r["id"] for r in rows} == {"c", "r"}

    def test_rows_carry_goal(self, client: _StubClient) -> None:
        client.seed("task_queue", [_running(id="r", goal="Implement #931", idempotency_key="k")])
        rows = list_active(client=client)
        assert rows[0]["goal"] == "Implement #931"

    def test_empty_queue(self, client: _StubClient) -> None:
        assert list_active(client=client) == []
