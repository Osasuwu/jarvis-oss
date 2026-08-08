"""Unit tests for dispatcher escalation triggers (issue #299, S2-4).

Pure-check tests use hand-rolled dict rows; DB-write tests use a stub
Supabase client that records the insert/update call shape. No live DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agents.escalation import (
    DISPATCHER_AGENT_ID,
    ESCALATION_EVENT_TYPE,
    ESCALATION_SEVERITY,
    PATTERN_REPEAT_THRESHOLD,
    STALE_APPROVAL_MAX_DAYS,
    EscalationCheck,
    EscalationContext,
    Trigger,
    check_all,
    check_cross_task_conflict,
    check_limit_near_exhaustion,
    check_pattern_repeat,
    check_scope_drift,
    check_stale_approval,
    escalate,
)
from agents.usage_probe import UsageReading


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


# Module-load snapshot — gives every test in this file a single,
# stable "current time" while still tracking real wall-clock so the
# production code's datetime.now(UTC) (called in check_all paths
# without explicit now= injection) sees a fresh approved_at.
_NOW = datetime.now(UTC)


def _now() -> datetime:
    return _NOW


def _queue_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "goal": "refactor: split usage_probe tests",
        "scope_files": ["tests/test_agents_usage_probe.py"],
        "approved_at": _now().isoformat(),
        "approved_by": "owner",
        "approved_scope_hash": "abc123",
        "auto_dispatch": True,
        "status": "pending",
        "idempotency_key": "deadbeef",
    }
    base.update(overrides)
    return base


def _reading(near: bool, used: int = 20, total: int = 100) -> UsageReading:
    return UsageReading(
        limit_window=timedelta(hours=5),
        used=used,
        total=total,
        reset_at=_now(),
        near_exhaustion=near,
    )


# ---------------------------------------------------------------------------
# check_stale_approval
# ---------------------------------------------------------------------------


def test_stale_approval_fresh_does_not_escalate() -> None:
    row = _queue_row(approved_at=(_now() - timedelta(days=1)).isoformat())
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is False


def test_stale_approval_exact_threshold_does_not_escalate() -> None:
    """Strictly >max_age_days escalates; == does not (conservative, honors owner's approval)."""
    row = _queue_row(approved_at=(_now() - timedelta(days=STALE_APPROVAL_MAX_DAYS)).isoformat())
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is False


def test_stale_approval_past_threshold_escalates() -> None:
    row = _queue_row(approved_at=(_now() - timedelta(days=STALE_APPROVAL_MAX_DAYS + 1)).isoformat())
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is True
    assert result.trigger == Trigger.STALE_APPROVAL
    assert result.context["age_days"] == STALE_APPROVAL_MAX_DAYS + 1
    assert result.context["max_age_days"] == STALE_APPROVAL_MAX_DAYS


def test_stale_approval_missing_approved_at_does_not_escalate() -> None:
    """No evidence to escalate from — don't fabricate one."""
    row = _queue_row()
    row.pop("approved_at")
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is False


def test_stale_approval_accepts_datetime_value() -> None:
    """Supabase sometimes returns already-parsed datetimes."""
    row = _queue_row(approved_at=_now() - timedelta(days=STALE_APPROVAL_MAX_DAYS + 1))
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is True


def test_stale_approval_bad_timestamp_does_not_escalate() -> None:
    row = _queue_row(approved_at="not-a-date")
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is False


def test_stale_approval_z_suffix_parses() -> None:
    """ISO string with 'Z' suffix (old-style UTC) must parse on 3.11+."""
    row = _queue_row(
        approved_at=(_now() - timedelta(days=STALE_APPROVAL_MAX_DAYS + 1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    result = check_stale_approval(row, now=_now())
    assert result.should_escalate is True


def test_stale_approval_max_age_days_is_configurable() -> None:
    row = _queue_row(approved_at=(_now() - timedelta(days=2)).isoformat())
    result = check_stale_approval(row, max_age_days=1, now=_now())
    assert result.should_escalate is True
    assert result.context["max_age_days"] == 1


# ---------------------------------------------------------------------------
# check_scope_drift
# ---------------------------------------------------------------------------


def test_scope_drift_same_hash_does_not_escalate() -> None:
    row = _queue_row(approved_scope_hash="abc123")
    result = check_scope_drift(row, current_scope_hash="abc123")
    assert result.should_escalate is False


def test_scope_drift_different_hash_escalates() -> None:
    row = _queue_row(approved_scope_hash="abc123")
    result = check_scope_drift(row, current_scope_hash="zzz999")
    assert result.should_escalate is True
    assert result.trigger == Trigger.SCOPE_DRIFT
    assert result.context == {"approved_scope_hash": "abc123", "current_scope_hash": "zzz999"}


def test_scope_drift_callable_hasher_is_invoked_with_scope_files() -> None:
    row = _queue_row(approved_scope_hash="abc123", scope_files=["a.py", "b.py"])
    seen: list[list[str]] = []

    def hasher(files: Any) -> str:
        seen.append(list(files))
        return "abc123"  # same -> no drift

    result = check_scope_drift(row, current_scope_hash=hasher)
    assert result.should_escalate is False
    assert seen == [["a.py", "b.py"]]


def test_scope_drift_hasher_exception_escalates_safely() -> None:
    """A broken hasher should escalate (treat as drift) rather than blowing up."""
    row = _queue_row(approved_scope_hash="abc123")

    def hasher(_files: Any) -> str:
        raise IOError("file vanished mid-scan")

    result = check_scope_drift(row, current_scope_hash=hasher)
    assert result.should_escalate is True
    assert result.trigger == Trigger.SCOPE_DRIFT
    assert "file vanished mid-scan" in result.context["error"]


def test_scope_drift_missing_approved_hash_does_not_escalate() -> None:
    row = _queue_row(approved_scope_hash="")
    result = check_scope_drift(row, current_scope_hash="zzz")
    assert result.should_escalate is False


# ---------------------------------------------------------------------------
# check_limit_near_exhaustion
# ---------------------------------------------------------------------------


def test_limit_escalates_when_probe_reports_near_exhaustion() -> None:
    result = check_limit_near_exhaustion(_reading(near=True, used=90, total=100))
    assert result.should_escalate is True
    assert result.trigger == Trigger.LIMIT_NEAR_EXHAUSTION
    assert result.context["used"] == 90
    assert result.context["total"] == 100
    assert 0.0 <= result.context["headroom_ratio"] <= 1.0


def test_limit_does_not_escalate_when_probe_reports_headroom() -> None:
    result = check_limit_near_exhaustion(_reading(near=False, used=10, total=100))
    assert result.should_escalate is False


# ---------------------------------------------------------------------------
# check_cross_task_conflict
# ---------------------------------------------------------------------------


def test_cross_task_conflict_overlap_escalates() -> None:
    row = _queue_row(id="me", scope_files=["a.py", "b.py"])
    others = [
        {"id": "other", "scope_files": ["b.py", "c.py"]},
    ]
    result = check_cross_task_conflict(row, active_dispatched_rows=others)
    assert result.should_escalate is True
    assert result.trigger == Trigger.CROSS_TASK_CONFLICT
    assert result.context["conflicting_task_id"] == "other"
    assert result.context["overlapping_files"] == ["b.py"]


def test_cross_task_conflict_disjoint_does_not_escalate() -> None:
    row = _queue_row(id="me", scope_files=["a.py"])
    others = [{"id": "other", "scope_files": ["x.py"]}]
    result = check_cross_task_conflict(row, active_dispatched_rows=others)
    assert result.should_escalate is False


def test_cross_task_conflict_same_id_is_ignored() -> None:
    """Defensive: even if dispatcher forgot to exclude this row, we do."""
    row = _queue_row(id="me", scope_files=["a.py"])
    result = check_cross_task_conflict(
        row, active_dispatched_rows=[{"id": "me", "scope_files": ["a.py"]}]
    )
    assert result.should_escalate is False


def test_cross_task_conflict_empty_scope_does_not_escalate() -> None:
    row = _queue_row(id="me", scope_files=[])
    others = [{"id": "other", "scope_files": ["a.py"]}]
    result = check_cross_task_conflict(row, active_dispatched_rows=others)
    assert result.should_escalate is False


def test_cross_task_conflict_first_overlap_wins() -> None:
    """Stability: we report the first conflicting peer, not all of them."""
    row = _queue_row(id="me", scope_files=["a.py"])
    others = [
        {"id": "peer1", "scope_files": ["a.py"]},
        {"id": "peer2", "scope_files": ["a.py"]},
    ]
    result = check_cross_task_conflict(row, active_dispatched_rows=others)
    assert result.context["conflicting_task_id"] == "peer1"


# ---------------------------------------------------------------------------
# check_pattern_repeat
# ---------------------------------------------------------------------------


def test_pattern_repeat_escalates_above_threshold() -> None:
    row = _queue_row(goal="auto-label cleanup")
    recent = [{"goal": "auto-label cleanup"}] * (PATTERN_REPEAT_THRESHOLD + 1)
    result = check_pattern_repeat(row, recent_successful_dispatches=recent)
    assert result.should_escalate is True
    assert result.trigger == Trigger.PATTERN_REPEAT
    assert result.context["recent_matching_dispatches"] == PATTERN_REPEAT_THRESHOLD + 1
    assert result.context["threshold"] == PATTERN_REPEAT_THRESHOLD


def test_pattern_repeat_at_threshold_does_not_escalate() -> None:
    """Issue says '> 3', so exactly 3 should pass."""
    row = _queue_row(goal="x")
    recent = [{"goal": "x"}] * PATTERN_REPEAT_THRESHOLD
    result = check_pattern_repeat(row, recent_successful_dispatches=recent)
    assert result.should_escalate is False


def test_pattern_repeat_different_goal_resets_run() -> None:
    """A different goal anywhere in the newest-first list breaks the run."""
    row = _queue_row(goal="x")
    recent = [
        {"goal": "x"},
        {"goal": "x"},
        {"goal": "different"},
        {"goal": "x"},
        {"goal": "x"},
    ]
    result = check_pattern_repeat(row, recent_successful_dispatches=recent)
    assert result.should_escalate is False


def test_pattern_repeat_missing_goal_does_not_escalate() -> None:
    row = _queue_row()
    row.pop("goal")
    result = check_pattern_repeat(row, recent_successful_dispatches=[{"goal": "whatever"}] * 10)
    assert result.should_escalate is False


def test_pattern_repeat_custom_threshold() -> None:
    row = _queue_row(goal="x")
    result = check_pattern_repeat(
        row, recent_successful_dispatches=[{"goal": "x"}, {"goal": "x"}], threshold=1
    )
    assert result.should_escalate is True


# ---------------------------------------------------------------------------
# check_all — aggregator
# ---------------------------------------------------------------------------


def test_check_all_returns_first_match_in_priority_order() -> None:
    """Stale approval is first; if both stale and scope drift fire, stale wins."""
    row = _queue_row(
        approved_at=(_now() - timedelta(days=STALE_APPROVAL_MAX_DAYS + 1)).isoformat(),
        approved_scope_hash="abc",
    )
    ctx = EscalationContext(
        current_scope_hash="different",  # drift would also fire
        usage_reading=_reading(near=False),
    )
    result = check_all(row, ctx)
    assert result.trigger == Trigger.STALE_APPROVAL


def test_check_all_no_triggers_returns_no_action() -> None:
    row = _queue_row()
    ctx = EscalationContext(
        current_scope_hash=row["approved_scope_hash"],
        usage_reading=_reading(near=False),
    )
    result = check_all(row, ctx)
    assert result.should_escalate is False


def test_check_all_runs_later_checks_when_earlier_pass() -> None:
    row = _queue_row()
    ctx = EscalationContext(
        current_scope_hash=row["approved_scope_hash"],  # no drift
        usage_reading=_reading(near=True, used=90, total=100),  # limit fires
    )
    result = check_all(row, ctx)
    assert result.trigger == Trigger.LIMIT_NEAR_EXHAUSTION


# ---------------------------------------------------------------------------
# escalate() — DB side effect via stub client.
# ---------------------------------------------------------------------------


@dataclass
class _StubResponse:
    data: list[dict[str, Any]]


class _StubUpdateQuery:
    def __init__(self, table: "_StubTable") -> None:
        self._table = table
        self._update: dict[str, Any] = {}

    def update(self, payload: dict[str, Any]) -> "_StubUpdateQuery":
        self._update = payload
        return self

    def eq(self, col: str, val: Any) -> "_StubUpdateQuery":
        self._table.calls.append(
            ("update", self._table.name, {"match": {col: val}, "set": self._update})
        )
        return self

    def execute(self) -> _StubResponse:
        return _StubResponse(data=[self._update])


class _StubInsertQuery:
    def __init__(self, table: "_StubTable", payload: dict[str, Any]) -> None:
        self._table = table
        self._payload = payload

    def execute(self) -> _StubResponse:
        stored = {**self._payload, "id": f"{self._table.name}-evt-1"}
        self._table.calls.append(("insert", self._table.name, self._payload))
        return _StubResponse(data=[stored])


class _StubTable:
    def __init__(self, name: str, calls: list[Any]) -> None:
        self.name = name
        self.calls = calls

    def insert(self, payload: dict[str, Any]) -> _StubInsertQuery:
        return _StubInsertQuery(self, payload)

    def update(self, payload: dict[str, Any]) -> _StubUpdateQuery:
        q = _StubUpdateQuery(self)
        return q.update(payload)


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def table(self, name: str) -> _StubTable:
        return _StubTable(name, self.calls)


def test_escalate_writes_event_with_expected_shape() -> None:
    client = _StubClient()
    row = _queue_row(id="11111111-1111-1111-1111-111111111111", goal="cleanup")
    check = EscalationCheck(
        should_escalate=True,
        trigger=Trigger.STALE_APPROVAL,
        context={"age_days": 9, "max_age_days": 7},
    )
    result = escalate(row, check, client=client)
    insert_calls = [c for c in client.calls if c[0] == "insert"]
    assert len(insert_calls) == 1
    _, table, payload = insert_calls[0]
    assert table == "events"
    assert payload["event_type"] == ESCALATION_EVENT_TYPE
    assert payload["severity"] == ESCALATION_SEVERITY
    assert payload["source"] == DISPATCHER_AGENT_ID
    assert payload["payload"]["queue_id"] == row["id"]
    assert payload["payload"]["trigger"] == Trigger.STALE_APPROVAL.value
    assert payload["payload"]["context"] == check.context
    # Title should carry both id and trigger for quick scanning.
    assert row["id"] in payload["title"]
    assert Trigger.STALE_APPROVAL.value in payload["title"]
    # And the returned event should have the stubbed id.
    assert "id" in result


def test_escalate_updates_queue_row_to_escalated() -> None:
    client = _StubClient()
    row = _queue_row(id="11111111-1111-1111-1111-111111111111")
    check = EscalationCheck(
        should_escalate=True,
        trigger=Trigger.SCOPE_DRIFT,
        context={"approved_scope_hash": "a", "current_scope_hash": "b"},
    )
    escalate(row, check, client=client)
    update_calls = [c for c in client.calls if c[0] == "update"]
    assert len(update_calls) == 1
    _, table, payload = update_calls[0]
    assert table == "task_queue"
    assert payload["match"] == {"id": row["id"]}
    assert payload["set"]["status"] == "escalated"
    assert "scope_drift" in payload["set"]["escalated_reason"]


def test_escalate_rejects_non_escalating_check() -> None:
    client = _StubClient()
    row = _queue_row()
    with pytest.raises(ValueError, match="non-escalating"):
        escalate(row, EscalationCheck.no_action(), client=client)


def test_escalate_without_id_skips_queue_update_but_writes_event() -> None:
    """An id-less row (hand-built in a test, hypothetically) still records the event."""
    client = _StubClient()
    row = _queue_row()
    row.pop("id")
    check = EscalationCheck(
        should_escalate=True,
        trigger=Trigger.LIMIT_NEAR_EXHAUSTION,
        context={"used": 90},
    )
    escalate(row, check, client=client)
    inserts = [c for c in client.calls if c[0] == "insert"]
    updates = [c for c in client.calls if c[0] == "update"]
    assert len(inserts) == 1
    assert len(updates) == 0
    assert inserts[0][2]["payload"]["queue_id"] is None


def test_escalate_context_round_trips_to_payload() -> None:
    """Nested dict context should survive into the events.payload jsonb."""
    client = _StubClient()
    row = _queue_row(id="x")
    context = {
        "conflicting_task_id": "peer",
        "overlapping_files": ["a.py", "b.py"],
        "nested": {"k": 1},
    }
    check = EscalationCheck(
        should_escalate=True, trigger=Trigger.CROSS_TASK_CONFLICT, context=context
    )
    escalate(row, check, client=client)
    insert = [c for c in client.calls if c[0] == "insert"][0]
    assert insert[2]["payload"]["context"] == context
