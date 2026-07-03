"""Tests for the deterministic event router (issue #744).

The router is a pure function of one ``events``-table row. No live model:
every assertion here pins a fixed (event_type, severity) input to its route.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agents import safety
from agents.orchestrator import (
    Decision,
    DispatchResult,
    EscalationNotice,
    InlineResult,
    Route,
    dispatch,
    escalation_notice,
    handle_event,
    priority_for,
    run_inline_tool,
)


def _ev(event_type: str, severity: str = "info", payload: dict | None = None) -> dict:
    return {
        "event_type": event_type,
        "severity": severity,
        "payload": payload or {},
    }


# -- Fake task_queue client (insert-only; simulates idempotency collision) --


class _FakeResult:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeInsert:
    def __init__(self, table: _FakeTable, payload: dict) -> None:
        self._table = table
        self._payload = payload

    def execute(self) -> _FakeResult:
        key = self._payload.get("idempotency_key")
        if any(r.get("idempotency_key") == key for r in self._table.rows):
            return _FakeResult([])  # unique-constraint collision → no row
        stored = {**self._payload, "id": f"tq-{len(self._table.rows) + 1}"}
        self._table.rows.append(stored)
        return _FakeResult([stored])


class _FakeTable:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert(self, payload: dict) -> _FakeInsert:
        return _FakeInsert(self, payload)


class _FakeClient:
    def __init__(self) -> None:
        self._tables: dict[str, _FakeTable] = {}

    def table(self, name: str) -> _FakeTable:
        return self._tables.setdefault(name, _FakeTable())


# Weekdays/weekends with a fixed, deterministic clock (no Date.now()).
_FRIDAY = datetime(2026, 5, 29, 10, 0, 0)  # weekday() == 4
_SATURDAY = datetime(2026, 5, 30, 10, 0, 0)  # weekday() == 5
_SUNDAY = datetime(2026, 5, 31, 10, 0, 0)  # weekday() == 6
_MONDAY = datetime(2026, 6, 1, 10, 0, 0)  # weekday() == 0


# -- AC1: deterministic route table ----------------------------------------


def test_security_alert_critical_escalates():
    assert handle_event(_ev("security_alert", "critical")).route is Route.ESCALATE


def test_ci_failure_high_emits_task():
    d = handle_event(_ev("ci_failure", "high", {"pr": 5}))
    assert d.route is Route.EMIT_TASK
    assert d.assignee == "sandcastle"


def test_review_negative_medium_emits_rework():
    d = handle_event(_ev("review_negative", "medium", {"pr": 7}))
    assert d.route is Route.EMIT_TASK
    assert d.assignee == "sandcastle"
    assert "/rework" in d.goal and "7" in d.goal


def test_global_task_due_low_emits_task():
    """global_task_due at severity='low' routes to EMIT_TASK."""
    d = handle_event(
        _ev(
            "global_task_due",
            "low",
            {
                "dispatcher_skill": "research",
                "output_sink": "memory",
                "lapse_intervals": 1,
            },
        )
    )
    assert d.route is Route.EMIT_TASK
    assert d.assignee == "sandcastle"
    assert "research" in d.goal


def test_global_task_due_defaults_dispatcher_skill_to_research():
    """A global_task_due payload missing dispatcher_skill defaults to 'research'
    (payload.get(..., 'research')) — the goal still names a concrete skill rather
    than emitting None."""
    d = handle_event(
        _ev("global_task_due", "low", {"output_sink": "memory", "lapse_intervals": 1})
    )
    assert d.route is Route.EMIT_TASK
    assert d.goal.startswith("global task: research")
    assert "None" not in d.goal


def test_global_task_due_goal_carries_source_and_body():
    """CRITICAL #2: the EMIT_TASK goal IS the spawned ``claude -p`` agent's
    prompt, so it must carry actionable context — the source row id (traceable,
    and distinct per source) and the task ``body`` (what to actually do) — not
    just the bare skill name. Before the fix the goal was ``"global task:
    research"`` with no source/body, so the agent had nothing to act on."""
    d = handle_event(
        _ev(
            "global_task_due",
            "low",
            {
                "dispatcher_skill": "research",
                "source_id": "src-42",
                "output_sink": "memory",
                "title": "Weekly arxiv sweep",
                "body": "Summarize this week's arxiv on agents.",
                "lapse_intervals": 1,
            },
        )
    )
    assert d.route is Route.EMIT_TASK
    assert "src-42" in d.goal
    assert "Summarize this week's arxiv on agents." in d.goal


def test_global_task_due_goal_unique_per_source():
    """MAJOR #2: two sources with the same dispatcher_skill must not collapse to
    an identical goal string (queue rows would be indistinguishable). The source
    id disambiguates them."""
    base = {"dispatcher_skill": "research", "output_sink": "memory", "body": "x"}
    g1 = handle_event(_ev("global_task_due", "low", {**base, "source_id": "a"})).goal
    g2 = handle_event(_ev("global_task_due", "low", {**base, "source_id": "b"})).goal
    assert g1 != g2


@pytest.mark.parametrize("severity", ["info", "medium", "high", "critical"])
def test_global_task_due_non_low_severity_failsafe_escalates(severity):
    """global_task_due is an enumerated route ONLY at severity='low'. Any other
    severity is an unknown (event_type, severity) pair → fail-safe escalate,
    never a silent EMIT_TASK at the wrong priority (MAJOR #11)."""
    d = handle_event(_ev("global_task_due", severity, {"dispatcher_skill": "research"}))
    assert d.route is Route.ESCALATE


@pytest.mark.parametrize("event_type", ["pr_approved", "pr_merged", "ci_success"])
def test_pipeline_events_are_inline_noop(event_type):
    d = handle_event(_ev(event_type, "info"))
    assert d.route is Route.HANDLE_INLINE
    assert d.noop is True


def test_unknown_event_type_failsafe_escalates():
    assert handle_event(_ev("totally_unknown", "high")).route is Route.ESCALATE


def test_known_type_unenumerated_severity_failsafe_escalates():
    # ci_failure only routes to emit_task at `high`; any other severity is an
    # unknown (event_type, severity) pair → fail-safe escalate.
    assert handle_event(_ev("ci_failure", "low")).route is Route.ESCALATE
    assert handle_event(_ev("review_negative", "high")).route is Route.ESCALATE


# -- AC4: security_alert is a safety floor the router cannot override -------


@pytest.mark.parametrize("severity", ["critical", "high", "medium", "low", "info"])
def test_security_alert_never_inline_at_any_severity(severity):
    d = handle_event(_ev("security_alert", severity))
    assert d.route is Route.ESCALATE
    assert d.route is not Route.HANDLE_INLINE


# -- AC2: priority = f(severity), strictly monotonic -----------------------


def test_priority_strictly_monotonic_by_severity():
    p = [priority_for(s) for s in ("info", "low", "medium", "high", "critical")]
    assert p == sorted(p)
    assert len(set(p)) == 5  # strictly increasing, no ties


def test_emit_task_priority_tracks_severity():
    assert handle_event(_ev("ci_failure", "high")).priority == priority_for("high")


# -- AC2: idempotency_key dedups re-delivery, re-runs genuinely-new events --


def test_idempotency_key_stable_for_identical_event():
    e = _ev("ci_failure", "high", {"pr": 5, "sha": "abc"})
    assert (
        handle_event(e).idempotency_key
        == handle_event(_ev("ci_failure", "high", {"pr": 5, "sha": "abc"})).idempotency_key
    )


def test_idempotency_key_differs_for_new_payload_state():
    k1 = handle_event(_ev("ci_failure", "high", {"pr": 5, "sha": "abc"})).idempotency_key
    k2 = handle_event(_ev("ci_failure", "high", {"pr": 5, "sha": "def"})).idempotency_key
    assert k1 != k2


def test_idempotency_key_differs_by_event_type():
    k1 = handle_event(_ev("ci_failure", "high", {"pr": 5})).idempotency_key
    k2 = handle_event(_ev("review_negative", "medium", {"pr": 5})).idempotency_key
    assert k1 != k2


# -- AC3: escalate decision carries owner assignee + reason + elevated pri --


def test_escalate_decision_fields():
    d = handle_event(_ev("security_alert", "critical", {"detail": "leaked key"}))
    assert d.route is Route.ESCALATE
    assert d.assignee == "owner"
    assert d.escalated_reason  # non-empty human-readable reason
    # elevated: an escalation outranks a same-severity emit_task
    assert d.priority >= priority_for("critical")


def test_decision_is_frozen():
    d = handle_event(_ev("pr_merged"))
    with pytest.raises(Exception):
        d.route = Route.ESCALATE  # type: ignore[misc]
    assert isinstance(d, Decision)


# ===========================================================================
# AC2 side-effect: dispatch(EMIT_TASK) writes a sandcastle row, dedups
# ===========================================================================


def test_dispatch_emit_task_writes_sandcastle_row():
    cli = _FakeClient()
    d = handle_event(_ev("ci_failure", "high", {"pr": 5, "sha": "abc"}))
    res = dispatch(d, now=_FRIDAY, client=cli)
    assert isinstance(res, DispatchResult)
    assert res.enqueued is True
    assert res.row is not None
    assert res.row["assignee"] == "sandcastle"
    assert res.row["priority"] == priority_for("high")
    assert res.row["idempotency_key"] == d.idempotency_key
    assert res.row["status"] == "pending"


def test_dispatch_emit_task_redelivery_dedups():
    cli = _FakeClient()
    e = _ev("ci_failure", "high", {"pr": 5, "sha": "abc"})
    first = dispatch(handle_event(e), now=_FRIDAY, client=cli)
    # Identical re-delivery → same idempotency_key → collision, no second row.
    second = dispatch(handle_event(dict(e)), now=_FRIDAY, client=cli)
    assert first.enqueued is True
    assert second.enqueued is False
    assert second.row is None
    assert len(cli.table("task_queue").rows) == 1


def test_dispatch_emit_task_new_event_reruns():
    cli = _FakeClient()
    first = dispatch(
        handle_event(_ev("ci_failure", "high", {"pr": 5, "sha": "abc"})),
        now=_FRIDAY,
        client=cli,
    )
    # A genuinely-new event (different sha) has a different key → re-runs.
    second = dispatch(
        handle_event(_ev("ci_failure", "high", {"pr": 5, "sha": "def"})),
        now=_FRIDAY,
        client=cli,
    )
    assert first.enqueued is True and second.enqueued is True
    assert len(cli.table("task_queue").rows) == 2


# ===========================================================================
# AC3 side-effect: escalate writes owner row + escalated_reason; weekend-aware
# ===========================================================================


def test_dispatch_escalate_writes_owner_row_with_reason():
    cli = _FakeClient()
    d = handle_event(_ev("security_alert", "critical", {"detail": "leaked key"}))
    res = dispatch(d, now=_FRIDAY, client=cli)
    assert res.row is not None
    assert res.row["assignee"] == "owner"
    assert res.row["escalated_reason"]  # persisted on the row
    assert res.row["priority"] >= priority_for("critical")


def test_escalation_notice_critical_pings_any_day():
    for day in (_FRIDAY, _SATURDAY, _SUNDAY, _MONDAY):
        assert escalation_notice("critical", day) is EscalationNotice.TELEGRAM_NOW


def test_escalation_notice_noncritical_weekend_parks_to_monday():
    assert escalation_notice("high", _SATURDAY) is EscalationNotice.PARK_MONDAY
    assert escalation_notice("medium", _SUNDAY) is EscalationNotice.PARK_MONDAY


def test_escalation_notice_noncritical_weekday_sessionstart():
    assert escalation_notice("high", _FRIDAY) is EscalationNotice.SESSIONSTART
    assert escalation_notice("low", _MONDAY) is EscalationNotice.SESSIONSTART


def test_dispatch_critical_fires_notifier():
    cli = _FakeClient()
    pinged: list[Decision] = []
    d = handle_event(_ev("security_alert", "critical", {"detail": "x"}))
    res = dispatch(d, now=_SATURDAY, client=cli, notifier=pinged.append)
    assert res.notice is EscalationNotice.TELEGRAM_NOW
    assert res.notified is True
    assert pinged == [d]


def test_dispatch_noncritical_weekend_does_not_ping():
    cli = _FakeClient()
    pinged: list[Decision] = []
    # Unknown (event_type, severity) → fail-safe escalate at non-critical sev.
    d = handle_event(_ev("some_unknown_event", "high"))
    assert d.route is Route.ESCALATE
    res = dispatch(d, now=_SATURDAY, client=cli, notifier=pinged.append)
    assert res.notice is EscalationNotice.PARK_MONDAY
    assert res.notified is False
    assert pinged == []
    # The owner row is still written — work is never lost, only the ping waits.
    assert res.row is not None and res.row["assignee"] == "owner"


# ===========================================================================
# AC5: inline tool surface routes through safety.gate (Tier 0 / 1 / 2)
# ===========================================================================


def test_run_inline_tier0_fires_fn():
    ran: list[str] = []
    res = run_inline_tool("audit_event", fn=lambda: ran.append("ran"))
    assert isinstance(res, InlineResult)
    assert res.tier is safety.Tier.AUTO
    assert res.fired is True
    assert ran == ["ran"]
    assert res.queued_owner_row is None


def test_run_inline_unmapped_tool_degrades_to_owner_row():
    cli = _FakeClient()
    ran: list[str] = []
    res = run_inline_tool("mystery_tool", fn=lambda: ran.append("ran"), client=cli)
    assert res.tier is safety.Tier.OWNER_QUEUE
    assert res.fired is False
    assert ran == []  # never auto-run an unvetted tool
    assert res.queued_owner_row is not None
    assert res.queued_owner_row["assignee"] == "owner"


def test_run_inline_tier2_blocks_and_audits():
    ran: list[str] = []
    # A destructive action classifies Tier 2 → gate raises, fn never runs.
    with pytest.raises(safety.GateError):
        run_inline_tool("delete", fn=lambda: ran.append("ran"))
    assert ran == []
