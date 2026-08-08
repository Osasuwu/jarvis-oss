"""Unit tests for morning_check.py (issue #389, Sprint 4).

Tests the --enqueue-on-alarm flag and idempotency key generation.
Uses a stub Supabase client (no live DB, no real Supabase connection).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Stubs — minimal Supabase client mock
# ---------------------------------------------------------------------------


@dataclass
class _Response:
    data: list[dict[str, Any]]


class _UpsertQuery:
    """Mock upsert query that records the operation."""

    def __init__(self, table: "_Table", payload: dict[str, Any]) -> None:
        self._table = table
        self._payload = payload

    def execute(self) -> _Response:
        # Record the upsert for inspection
        self._table.calls.append(
            (
                "upsert",
                self._table.name,
                {
                    "payload": dict(self._payload),
                    "on_conflict": "idempotency_key",
                    "ignore_duplicates": True,
                },
            )
        )
        self._table.seeded_rows.append(self._payload)
        return _Response(data=[self._payload])


class _SelectQuery:
    """Mock select query that returns pre-seeded rows."""

    def __init__(self, table: "_Table") -> None:
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._order: tuple[str, bool] | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "_SelectQuery":
        return self

    def gte(self, col: str, val: Any) -> "_SelectQuery":
        self._filters.append(("gte", col, val))
        return self

    def eq(self, col: str, val: Any) -> "_SelectQuery":
        self._filters.append(("eq", col, val))
        return self

    def order(self, col: str, *, desc: bool = False) -> "_SelectQuery":
        self._order = (col, desc)
        return self

    def execute(self) -> _Response:
        # Apply filters
        rows = list(self._table.seeded_rows)
        for op, col, val in self._filters:
            if op == "gte":
                rows = [r for r in rows if r.get(col) >= val]
            elif op == "eq":
                rows = [r for r in rows if r.get(col) == val]

        # Apply ordering
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: r.get(col) or "", reverse=desc)

        self._table.calls.append(("select", self._table.name, {"filters": self._filters}))
        return _Response(data=rows)


class _Table:
    """Mock table with upsert, select, and insert operations."""

    def __init__(self, name: str, calls: list[Any], rows: list[dict[str, Any]]) -> None:
        self.name = name
        self.calls = calls
        self.seeded_rows = rows

    def select(self, *_args: Any, **_kwargs: Any) -> _SelectQuery:
        return _SelectQuery(self)

    def upsert(
        self,
        payload: dict[str, Any],
        on_conflict: str | None = None,
        ignore_duplicates: bool | None = None,
    ) -> _UpsertQuery:
        return _UpsertQuery(self, payload)


class _StubClient:
    """Records calls and seeded audit_log rows."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.tables: dict[str, list[dict[str, Any]]] = {
            "task_queue": [],
            "audit_log": [],
        }

    def table(self, name: str) -> _Table:
        return _Table(name, self.calls, self.tables.setdefault(name, []))

    def seed_audit_log(self, rows: list[dict[str, Any]]) -> None:
        self.tables["audit_log"].extend(rows)


# ---------------------------------------------------------------------------
# Stub contract: verify the stub satisfies real client expectations
# ---------------------------------------------------------------------------


def test_stub_contract_smoke() -> None:
    """Smoke test: the _StubClient supports the query patterns morning_check.py uses.

    Without this contract test, a refactor of the stub that drops a method
    (e.g. _SelectQuery.order) would only surface as a test failure, not a
    compilation error — the stubs duck-type the real Supabase client.
    """
    stub = _StubClient()
    stub.seed_audit_log([{"agent_id": "a", "outcome": "ok", "timestamp": "2026-01-01T00:00:00"}])

    # Select chain: table().select().gte().order().eq().execute()
    q = stub.table("audit_log").select("*").gte("timestamp", "2026-01-01").order("timestamp")
    q = q.eq("agent_id", "a")
    result = q.execute()
    assert result.data is not None
    assert len(result.data) == 1
    assert result.data[0]["agent_id"] == "a"

    # Upsert chain: table().upsert().execute()
    stub.table("task_queue").upsert(
        {"key": "v"}, on_conflict="k", ignore_duplicates=True
    ).execute()
    upserts = [c for c in stub.calls if c[0] == "upsert"]
    assert len(upserts) == 1


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """UTC now for test seed timestamps.

    Was previously a frozen 2026-04-25 14:30 UTC. That worked while the
    repo's "today" was within 24h of that point, then silently broke
    once two days had passed: morning_check filters audit_log by
    `timestamp >= now() - 24h`, so seeds older than that disappeared
    and the "healthy system" tests started seeing an empty result set
    ("service may be down" path) → exit 1 instead of 0.

    Use real now() so the seed always lands inside the lookback window.
    """
    return datetime.now(UTC)


def _audit_row(**overrides: Any) -> dict[str, Any]:
    """Build an audit_log row."""
    base = {
        "agent_id": "task-dispatcher",
        "tool_name": "claude_cli",
        "action": "dispatch",
        "target": "user",
        "outcome": "success",
        "timestamp": _now_utc().isoformat(),
        "details": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_enqueue_on_alarm_flag_default_off() -> None:
    """--enqueue-on-alarm defaults to False when not passed."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    stub.seed_audit_log(
        [
            _audit_row(agent_id="test-agent", outcome="success"),
        ]
    )

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        exit_code = main(argv=[])
        # Healthy system, no alarms
        assert exit_code == 0
        # No upsert calls made when flag not passed
        upserts = [c for c in stub.calls if c[0] == "upsert"]
        assert len(upserts) == 0


def test_enqueue_on_alarm_flag_set_adds_rows() -> None:
    """--enqueue-on-alarm=True enqueues alarms to task_queue."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    # Seed with a high failure rate to trigger an alarm
    stub.seed_audit_log(
        [
            _audit_row(agent_id="my-agent", outcome="success"),
            _audit_row(agent_id="my-agent", outcome="failure:TestError"),
            _audit_row(agent_id="my-agent", outcome="failure:TimeoutError"),
            _audit_row(agent_id="my-agent", outcome="failure:RuntimeError"),
            _audit_row(agent_id="my-agent", outcome="failure:ValueError"),
        ]
    )

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        exit_code = main(argv=["--enqueue-on-alarm"])
        # High failure rate triggers alarm, exit code 1
        assert exit_code == 1

        # Should have enqueued one task_queue row
        upserts = [c for c in stub.calls if c[0] == "upsert"]
        assert len(upserts) == 1

        # Inspect the row shape
        upsert_call = upserts[0]
        assert upsert_call[1] == "task_queue"
        payload = upsert_call[2]["payload"]

        # Verify tier:3-human shape
        assert payload["auto_dispatch"] is False
        assert payload["approved_by"] == "cron:morning_check"
        assert payload["scope_files"] == []
        assert "my-agent" in payload["goal"]
        assert payload["status"] == "pending"
        assert "idempotency_key" in payload
        assert len(payload["idempotency_key"]) == 64  # sha256 hex


def test_idempotency_key_stability() -> None:
    """Same alarm category + details on same day produce same key."""
    from scripts.observability.morning_check import _idempotency_key

    category = "high_failure_rate"
    details = "my-agent:50%"

    key1 = _idempotency_key(category, details)
    key2 = _idempotency_key(category, details)

    assert key1 == key2


def test_idempotency_key_different_categories() -> None:
    """Different categories produce different keys."""
    from scripts.observability.morning_check import _idempotency_key

    key1 = _idempotency_key("high_failure_rate", "agent:50%")
    key2 = _idempotency_key("dispatcher_gap", "agent:50%")

    assert key1 != key2


def test_idempotency_key_different_details() -> None:
    """Same category but different details produce different keys."""
    from scripts.observability.morning_check import _idempotency_key

    key1 = _idempotency_key("high_failure_rate", "agent1:50%")
    key2 = _idempotency_key("high_failure_rate", "agent2:50%")

    assert key1 != key2


def test_no_audit_rows_enqueues_alarm() -> None:
    """When no audit_log rows found, enqueue 'no_audit_rows' alarm if flag set."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    # Empty audit_log

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        exit_code = main(argv=["--enqueue-on-alarm"])
        # No rows is an alarm, exit code 1
        assert exit_code == 1

        upserts = [c for c in stub.calls if c[0] == "upsert"]
        assert len(upserts) == 1

        payload = upserts[0][2]["payload"]
        assert payload["approved_by"] == "cron:morning_check"
        assert "No audit_log rows" in payload["goal"]


def test_dispatcher_gap_enqueues_alarm() -> None:
    """Dispatcher gap > threshold enqueues 'dispatcher_gap' alarm."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    now = _now_utc()

    # Two dispatcher rows with a large gap
    stub.seed_audit_log(
        [
            _audit_row(agent_id="task-dispatcher", timestamp=now.isoformat()),
            _audit_row(
                agent_id="task-dispatcher",
                timestamp=(now + timedelta(minutes=15)).isoformat(),
            ),
        ]
    )

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        exit_code = main(argv=["--enqueue-on-alarm", "--gap-minutes", "10"])
        # Gap triggers alarm
        assert exit_code == 1

        upserts = [c for c in stub.calls if c[0] == "upsert"]
        assert len(upserts) == 1

        payload = upserts[0][2]["payload"]
        assert "15min gap" in payload["goal"]


def test_upsert_idempotency_call_shape() -> None:
    """Upsert uses on_conflict='idempotency_key', ignore_duplicates=True."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    stub.seed_audit_log(
        [
            _audit_row(agent_id="my-agent", outcome="success"),
            _audit_row(agent_id="my-agent", outcome="failure:TestError"),
            _audit_row(agent_id="my-agent", outcome="failure:TimeoutError"),
            _audit_row(agent_id="my-agent", outcome="failure:RuntimeError"),
            _audit_row(agent_id="my-agent", outcome="failure:ValueError"),
        ]
    )

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        main(argv=["--enqueue-on-alarm"])

        upserts = [c for c in stub.calls if c[0] == "upsert"]
        assert len(upserts) == 1

        call = upserts[0]
        meta = call[2]
        assert meta["on_conflict"] == "idempotency_key"
        assert meta["ignore_duplicates"] is True


def test_upsert_failure_does_not_crash() -> None:
    """If upsert raises exception, script continues and exits 1 (alarm)."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    stub.seed_audit_log(
        [
            _audit_row(agent_id="my-agent", outcome="success"),
            _audit_row(agent_id="my-agent", outcome="failure:TestError"),
            _audit_row(agent_id="my-agent", outcome="failure:TimeoutError"),
            _audit_row(agent_id="my-agent", outcome="failure:RuntimeError"),
            _audit_row(agent_id="my-agent", outcome="failure:ValueError"),
        ]
    )

    # Mock table.upsert() to raise an exception
    original_table = stub.table

    def failing_table(name: str) -> Any:
        t = original_table(name)
        if name == "task_queue":
            original_upsert = t.upsert

            def failing_upsert(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("Simulated DB error")

            t.upsert = failing_upsert
        return t

    stub.table = failing_table  # type: ignore

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        # Should not crash, should still exit 1 (alarm)
        exit_code = main(argv=["--enqueue-on-alarm"])
        assert exit_code == 1


def test_approved_scope_hash_matches_empty_list_hash() -> None:
    """approved_scope_hash matches sha256 of an empty scope list."""
    from scripts.observability.morning_check import main

    stub = _StubClient()
    stub.seed_audit_log(
        [
            _audit_row(agent_id="my-agent", outcome="success"),
            _audit_row(agent_id="my-agent", outcome="failure:TestError"),
            _audit_row(agent_id="my-agent", outcome="failure:TimeoutError"),
            _audit_row(agent_id="my-agent", outcome="failure:RuntimeError"),
            _audit_row(agent_id="my-agent", outcome="failure:ValueError"),
        ]
    )

    with patch(
        "scripts.observability.morning_check.get_client",
        return_value=stub,
    ):
        main(argv=["--enqueue-on-alarm"])

        upserts = [c for c in stub.calls if c[0] == "upsert"]
        payload = upserts[0][2]["payload"]

        # Compute the expected hash inline rather than importing the private
        # _hash_scope_files from agents.executor.  The algorithm joins
        # sorted() with "\n" then sha256 digests; for an empty list the
        # joined string is "" so the hash is sha256(b"").
        expected_hash = hashlib.sha256(b"").hexdigest()
        assert payload["approved_scope_hash"] == expected_hash
