"""Tests for telegram-notify-hook.py — high-severity event drain (#327, #649).

Test-B1 pins the re-send-loop fix (#649): mark_processed must transition the FSM
``state`` column to ``'processed'`` so a sent event is not re-fetched. AC-B2 locks
the fail-loud-on-missing-secrets contract against regression across the
reactive-core rewire.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Load telegram-notify-hook module from file (hyphenated filename).
spec = importlib.util.spec_from_file_location(
    "telegram_notify_hook",
    Path(__file__).parent.parent.parent / "scripts" / "telegram-notify-hook.py",
)
telegram_hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(telegram_hook)


# ---------------------------------------------------------------------------
# Minimal in-memory fake of the Supabase `events` table. Models exactly the
# query chains fetch_pending_events / mark_processed use, so the state
# transition (pending → processed) and the state-based filter are exercised
# end-to-end without a live DB.
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, rows, mode, payload=None):
        self._rows = rows  # shared list, mutated in place on update
        self._mode = mode  # "select" | "update"
        self._payload = payload
        self._eq: list[tuple[str, object]] = []
        self._in: list[tuple[str, list]] = []
        self._order: str | None = None
        self._desc = False
        self._limit: int | None = None

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in.append((col, list(vals)))
        return self

    def order(self, col, desc=False):
        self._order = col
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row) -> bool:
        for col, val in self._eq:
            if row.get(col) != val:
                return False
        for col, vals in self._in:
            if row.get(col) not in vals:
                return False
        return True

    def execute(self):
        matched = [r for r in self._rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return SimpleNamespace(data=[dict(r) for r in matched])
        if self._order is not None:
            matched.sort(key=lambda r: r.get(self._order) or "", reverse=self._desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return SimpleNamespace(data=[dict(r) for r in matched])


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return _FakeQuery(self._rows, "select")

    def update(self, payload):
        return _FakeQuery(self._rows, "update", payload)


class FakeEventsClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "events", f"unexpected table {name!r}"
        return _FakeTable(self.rows)


def _pending_event(event_id="e1", severity="high"):
    return {
        "id": event_id,
        "event_type": "flag_stale",
        "severity": severity,
        "repo": "Osasuwu/jarvis",
        "source": "autonomous-loop",
        "title": "stale flag",
        "payload": {},
        "created_at": "2026-07-08T00:00:00+00:00",
        "state": "pending",
    }


# ---------------------------------------------------------------------------
# Test-B1 (#649): a sent event ends state='processed' and is not re-fetched.
# ---------------------------------------------------------------------------


def test_mark_processed_transitions_state_to_processed():
    client = FakeEventsClient([_pending_event("e1")])
    telegram_hook.mark_processed(client, "e1", "telegram sent: ok")
    assert client.rows[0]["state"] == "processed"


def test_sent_event_not_refetched_by_second_drain():
    """The re-send loop is closed: fetch → send → mark → fetch again returns []."""
    client = FakeEventsClient([_pending_event("e1")])

    first = telegram_hook.fetch_pending_events(client, "high", 20)
    assert [e["id"] for e in first] == ["e1"]

    telegram_hook.mark_processed(client, "e1", "telegram sent: ok")

    second = telegram_hook.fetch_pending_events(client, "high", 20)
    assert second == []


def test_mark_processed_guards_on_pending_state():
    """A row already past 'pending' (claimed by the orchestrator) is not clobbered."""
    row = _pending_event("e1")
    row["state"] = "claimed"
    client = FakeEventsClient([row])

    telegram_hook.mark_processed(client, "e1", "telegram sent: ok")

    # Guarded on .eq('state','pending') → claimed row untouched.
    assert client.rows[0]["state"] == "claimed"


# ---------------------------------------------------------------------------
# Test-B3 (PR #1142 follow-up): mark_processed must distinguish a benign no-op
# (returns True) from a real DB/network failure (returns False + ERROR). A
# swallowed failure would silently reintroduce the #649 re-send loop.
# ---------------------------------------------------------------------------


class _RaisingClient:
    """Supabase stand-in whose events update raises — models a DB/network fault."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def table(self, name):
        assert name == "events", f"unexpected table {name!r}"
        return self

    def update(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        raise self._exc


def test_mark_processed_returns_true_on_success():
    client = FakeEventsClient([_pending_event("e1")])
    assert telegram_hook.mark_processed(client, "e1", "telegram sent: ok") is True


def test_mark_processed_returns_true_on_benign_no_match():
    """Row already claimed elsewhere → empty update, no exception → benign True."""
    row = _pending_event("e1")
    row["state"] = "claimed"
    client = FakeEventsClient([row])
    assert telegram_hook.mark_processed(client, "e1", "telegram sent: ok") is True


def test_mark_processed_returns_false_on_db_failure(capsys):
    """A real exception is surfaced (ERROR), not swallowed as a soft WARN."""
    client = _RaisingClient(RuntimeError("connection reset"))
    assert telegram_hook.mark_processed(client, "e1", "telegram sent: ok") is False
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "will re-notify" in err.lower()


def test_main_flags_run_failed_when_mark_fails(monkeypatch):
    """Send succeeds but the processed-write fails → exit 2, not a clean 0."""
    monkeypatch.setattr("sys.argv", ["telegram-notify-hook.py"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot:token")
    monkeypatch.setenv("TELEGRAM_ALLOW_USER_ID", "12345")
    monkeypatch.setenv("SUPABASE_URL", "http://localhost")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    with (
        patch("supabase.create_client", return_value=MagicMock()),
        patch.object(
            telegram_hook, "fetch_pending_events", return_value=[_pending_event("e1")]
        ),
        patch.object(telegram_hook, "send_telegram", return_value=(True, "sent")),
        patch.object(telegram_hook, "mark_processed", return_value=False),
    ):
        rc = telegram_hook.main()
    assert rc == 2


# ---------------------------------------------------------------------------
# AC-B2 (#649): fail loud on missing Telegram secrets. Non-dry-run with either
# TELEGRAM_BOT_TOKEN or TELEGRAM_ALLOW_USER_ID unset must exit non-zero BEFORE
# any send. Behavior already lives at telegram-notify-hook.py:168; these lock
# it against regression across the reactive-core rewire.
# ---------------------------------------------------------------------------


def test_main_fails_loud_without_bot_token(monkeypatch):
    monkeypatch.setattr("sys.argv", ["telegram-notify-hook.py"])
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOW_USER_ID", "12345")
    with (
        patch.object(telegram_hook, "send_telegram") as mock_send,
        patch.object(telegram_hook, "fetch_pending_events") as mock_fetch,
    ):
        rc = telegram_hook.main()
    assert rc == 1
    mock_send.assert_not_called()
    mock_fetch.assert_not_called()


def test_main_fails_loud_without_chat_id(monkeypatch):
    monkeypatch.setattr("sys.argv", ["telegram-notify-hook.py"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot:token")
    monkeypatch.delenv("TELEGRAM_ALLOW_USER_ID", raising=False)
    with (
        patch.object(telegram_hook, "send_telegram") as mock_send,
        patch.object(telegram_hook, "fetch_pending_events") as mock_fetch,
    ):
        rc = telegram_hook.main()
    assert rc == 1
    mock_send.assert_not_called()
    mock_fetch.assert_not_called()


def test_main_dry_run_does_not_fail_on_missing_secrets(monkeypatch):
    """--dry-run keeps its preview role even with no Telegram secrets set."""
    monkeypatch.setattr("sys.argv", ["telegram-notify-hook.py", "--dry-run"])
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOW_USER_ID", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "http://localhost")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    with (
        patch("supabase.create_client", return_value=MagicMock()),
        patch.object(telegram_hook, "fetch_pending_events", return_value=[]) as mock_fetch,
        patch.object(telegram_hook, "send_telegram") as mock_send,
    ):
        rc = telegram_hook.main()
    assert rc == 0
    mock_fetch.assert_called_once()
    mock_send.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
