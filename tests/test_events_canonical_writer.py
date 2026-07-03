"""Unit tests for the C17 events_canonical writer + buffer (#477).

Stub-based — no live Supabase. Verifies:
- Happy path: emit_event inserts a row + returns the response.
- Failure path: emit_event buffers, returns None, does NOT raise.
- Drain path: buffered events replay with degraded=true on next success.
- ContextVar bridge: caller-omitted trace_id is synthesized.
- Buffer overflow: oldest events drop (logged, not raised).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-memory"))

from events_canonical import (  # noqa: E402
    _BUFFER_MAX,
    _buffer_clear_for_test,
    _buffer_len_for_test,
    emit_event,
)
from trace_context import new_trace, with_trace  # noqa: E402

# ---------------------------------------------------------------------------
# RecordingClient — Supabase-shaped test double
# ---------------------------------------------------------------------------


class _RecordingResponse:
    """Fake response returned by RecordingClient.execute()."""

    def __init__(self, data: list[dict]) -> None:
        self.data = data


class RecordingClient:
    """Supabase-shaped test double that records inserted rows.

    Captures all rows passed to insert() so tests can assert on
    values directly instead of mock call-chain inspection.

    Usage::

        client = RecordingClient()
        client.table("events_canonical").insert(row).execute()
        assert client.inserts == [row]
    """

    def __init__(
        self,
        *,
        fail_on_insert: bool = False,
        return_data: list[dict] | None = None,
    ) -> None:
        self.inserts: list[dict] = []
        self.tables: list[str] = []
        self._fail_on_insert = fail_on_insert
        self._return_data = return_data

    def table(self, name: str) -> RecordingClient:
        self.tables.append(name)
        return self

    def insert(self, row: dict) -> RecordingClient:
        if self._fail_on_insert:
            raise RuntimeError("simulated failure")
        self.inserts.append(row)
        return self

    def execute(self) -> _RecordingResponse:
        if self._return_data is not None:
            return _RecordingResponse(self._return_data)
        return _RecordingResponse(
            [{"event_id": "stub-event-id", "trace_id": "stub-trace-id"}]
        )


# ---------------------------------------------------------------------------
# Buffer isolation per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_buffer() -> None:
    """Each test starts with an empty buffer."""
    _buffer_clear_for_test()
    yield
    _buffer_clear_for_test()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_emit_event_inserts_and_returns_row() -> None:
    client = RecordingClient(
        return_data=[{"event_id": "abc", "trace_id": "xyz"}]
    )
    result = emit_event(
        client,
        actor="skill:test",
        action="decision_made",
        payload={"foo": 1},
        outcome="success",
    )
    assert result == {"event_id": "abc", "trace_id": "xyz"}
    assert "events_canonical" in client.tables


def test_emit_event_uses_context_trace_id() -> None:
    """When context is set, writer picks it up automatically."""
    client = RecordingClient()
    tid = new_trace()
    with with_trace(tid):
        emit_event(
            client,
            actor="skill:test",
            action="memory_recall",
        )
    inserted = client.inserts[0]
    assert inserted["trace_id"] == tid


def test_emit_event_synthesizes_trace_when_context_missing() -> None:
    """No ContextVar set → writer mints a fresh trace_id, doesn't crash."""
    client = RecordingClient()
    emit_event(client, actor="skill:test", action="orphan_action")
    inserted = client.inserts[0]
    assert isinstance(inserted["trace_id"], str)
    assert len(inserted["trace_id"]) == 32  # hex


def test_emit_event_includes_optional_cost_fields_when_provided() -> None:
    client = RecordingClient()
    emit_event(
        client,
        actor="skill:test",
        action="decision_made",
        cost_tokens=1234,
        cost_usd=0.0125,
    )
    inserted = client.inserts[0]
    assert inserted["cost_tokens"] == 1234
    assert inserted["cost_usd"] == 0.0125


def test_emit_event_omits_cost_fields_when_not_provided() -> None:
    """Cost fields default to NULL — omit from row dict, don't pass None."""
    client = RecordingClient()
    emit_event(client, actor="skill:test", action="decision_made")
    inserted = client.inserts[0]
    assert "cost_tokens" not in inserted
    assert "cost_usd" not in inserted


def test_emit_event_caller_overrides_context() -> None:
    """Explicit trace_id wins over ContextVar."""
    client = RecordingClient()
    ctx_id = new_trace()
    explicit = new_trace()
    with with_trace(ctx_id):
        emit_event(
            client,
            actor="skill:test",
            action="x",
            trace_id=explicit,
        )
    inserted = client.inserts[0]
    assert inserted["trace_id"] == explicit


# ---------------------------------------------------------------------------
# Failure path — buffer
# ---------------------------------------------------------------------------


def test_emit_event_does_not_raise_on_insert_exception() -> None:
    """Caller MUST NOT see substrate failures."""
    client = RecordingClient(fail_on_insert=True)
    result = emit_event(client, actor="skill:test", action="x")
    assert result is None


def test_emit_event_buffers_on_failure() -> None:
    client = RecordingClient(fail_on_insert=True)
    assert _buffer_len_for_test() == 0
    emit_event(client, actor="skill:test", action="x")
    assert _buffer_len_for_test() == 1


def test_emit_event_returns_none_on_empty_data_response() -> None:
    """Defensive — Supabase returning empty data is a soft failure."""
    client = RecordingClient(return_data=[])
    result = emit_event(client, actor="skill:test", action="x")
    assert result is None
    assert _buffer_len_for_test() == 1


# ---------------------------------------------------------------------------
# Drain path
# ---------------------------------------------------------------------------


def test_buffered_events_drain_on_next_success() -> None:
    """A failure followed by a success drains the buffer with degraded=true."""
    # First call: fails, buffers.
    bad_client = RecordingClient(fail_on_insert=True)
    emit_event(bad_client, actor="skill:test", action="first")
    assert _buffer_len_for_test() == 1

    # Second call: succeeds — should drain the buffered row first.
    good_client = RecordingClient()
    emit_event(good_client, actor="skill:test", action="second")

    inserts = good_client.inserts
    # Two inserts on good client: drained replay + the new one.
    assert len(inserts) == 2
    drained, new = inserts
    assert drained["action"] == "first"
    assert drained["degraded"] is True
    assert new["action"] == "second"
    assert "degraded" not in new or new["degraded"] is False
    assert _buffer_len_for_test() == 0


def test_drain_failure_keeps_row_in_buffer() -> None:
    """If drain replay also fails, the row stays buffered for next attempt."""
    bad1 = RecordingClient(fail_on_insert=True)
    emit_event(bad1, actor="skill:test", action="first")
    assert _buffer_len_for_test() == 1

    bad2 = RecordingClient(fail_on_insert=True)
    emit_event(bad2, actor="skill:test", action="second")
    assert _buffer_len_for_test() == 2


def test_buffer_overflow_drops_oldest() -> None:
    """When the buffer fills, oldest events drop (FIFO)."""
    bad = RecordingClient(fail_on_insert=True)
    for i in range(_BUFFER_MAX + 5):
        emit_event(bad, actor="skill:test", action=f"event-{i}")
    assert _buffer_len_for_test() == _BUFFER_MAX
