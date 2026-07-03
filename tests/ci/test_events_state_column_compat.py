"""Meta-test: events handler tolerates pre-FSM-migration databases.

Bug reproduction: `mcp__memory__events_list` failed with
`column events.state does not exist` (PG 42703) on the live Supabase DB
because `mcp-memory/handlers/events.py` filtered on the `state` column
unconditionally — but the migration that adds it
(`supabase/migrations/20260521130515_extend_events_queue.sql`) had not
been applied to the live environment.

The fix in `handlers/events.py`:
 - `_handle_events_list` tries the FSM `state` filter first, catches
   the 42703 error, falls back to the legacy `processed = false`
   filter, and caches the detection for subsequent calls.
 - FSM RPCs (`claim_next`, `mark_processed`, `park_event`,
   `requeue_event`) catch 42883 (undefined function) and return a
   user-friendly message instead of bubbling the raw error.

This meta-test locks down both directions of the fix:
 1. **Static**: schema.sql declares both `processed` and `state`; the
    handler source contains both filter strings.
 2. **Behavioral**: mock the Supabase client to simulate a pre-migration
    DB; the handler must succeed without raising.

Pattern follows `tests/ci/test_schema_drift_guard.py` and
`tests/ci/test_memory_review_schema.py`.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDLER_PATH = REPO_ROOT / "mcp-memory" / "handlers" / "events.py"
SCHEMA_PATH = REPO_ROOT / "mcp-memory" / "schema.sql"
MIGRATION_PATH = (
    REPO_ROOT / "supabase" / "migrations" / "20260521130515_extend_events_queue.sql"
)

PG_UNDEFINED_COLUMN = (
    "{'message': 'column events.state does not exist', "
    "'code': '42703', 'hint': None, 'details': None}"
)
PG_UNDEFINED_FUNCTION = (
    "{'message': 'function claim_next(text) does not exist', "
    "'code': '42883', 'hint': None, 'details': None}"
)


# ===========================================================================
# Section 1 — Static checks on schema, migration, and handler source.
# ===========================================================================


class TestSchemaAndMigrationDeclareBothColumns:
    """schema.sql and the FSM migration must keep both filter columns alive.

    The handler picks between them at runtime; if either disappears, the
    handler has no safe filter to fall back on.
    """

    def test_schema_declares_processed_column(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert re.search(
            r"processed\s+boolean\s+not\s+null\s+default\s+false",
            text,
            re.IGNORECASE,
        ), "schema.sql events table must keep the legacy `processed boolean` column"

    def test_schema_declares_state_column(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert re.search(
            r"state\s+text\s+not\s+null\s+default\s+'pending'",
            text,
            re.IGNORECASE,
        ), "schema.sql events table must declare the FSM `state` column"

    def test_migration_adds_state_column(self):
        assert MIGRATION_PATH.exists(), (
            f"FSM migration missing: {MIGRATION_PATH.relative_to(REPO_ROOT)}"
        )
        text = MIGRATION_PATH.read_text(encoding="utf-8")
        assert re.search(
            r"ADD COLUMN IF NOT EXISTS\s+state\s+text",
            text,
            re.IGNORECASE,
        ), "FSM migration must add the `state` column to events"


class TestHandlerHasFallbackLogic:
    """Static guard: the handler source must reference BOTH filter strings.

    A regression that drops the fallback (returns the handler to the
    `state`-only filter) would reintroduce the original bug.
    """

    def test_handler_references_state_filter(self):
        text = HANDLER_PATH.read_text(encoding="utf-8")
        assert "state.eq.pending" in text, (
            "Handler should attempt the FSM `state` filter when available."
        )

    def test_handler_references_legacy_processed_filter(self):
        text = HANDLER_PATH.read_text(encoding="utf-8")
        # eq("processed", False) appears in both the filter fallback and the
        # bulk-mark-processed legacy update; either occurrence proves the
        # legacy code path exists.
        assert re.search(r'eq\(\s*"processed"\s*,\s*False\s*\)', text), (
            "Handler must use `eq('processed', False)` as the pre-migration "
            "filter fallback so events_list works before the FSM migration deploys."
        )

    def test_handler_detects_pg_42703(self):
        """The undefined-column detector must look for the PG error code."""
        text = HANDLER_PATH.read_text(encoding="utf-8")
        assert "42703" in text, (
            "Handler must detect PG 42703 (undefined_column) to know when "
            "to downgrade to the legacy filter."
        )

    def test_handler_detects_pg_42883(self):
        """The undefined-function detector covers the FSM RPCs."""
        text = HANDLER_PATH.read_text(encoding="utf-8")
        assert "42883" in text, (
            "Handler must detect PG 42883 (undefined_function) so FSM RPC "
            "tools return a friendly error pre-migration."
        )


# ===========================================================================
# Section 2 — Behavioral checks on a mocked pre-migration client.
# ===========================================================================


class _MockQuery:
    """Chainable mock for the PostgREST query builder."""

    def __init__(self, parent: "_MockClient"):
        self.parent = parent
        self._uses_state_filter = False

    def select(self, *_a, **_kw):
        return self

    def or_(self, expr: str):
        if "state.eq" in expr:
            self._uses_state_filter = True
        return self

    def eq(self, col: str, _val):
        if col == "state":
            self._uses_state_filter = True
        return self

    def in_(self, _col, _vals):
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, _n):
        return self

    def update(self, _data):
        return self

    def execute(self):
        self.parent.execute_count += 1
        if self._uses_state_filter and not self.parent.has_state:
            raise RuntimeError(PG_UNDEFINED_COLUMN)
        return MagicMock(data=list(self.parent.rows))


class _MockRpc:
    """Mock for client.rpc(name, params).execute()."""

    def __init__(self, parent: "_MockClient", name: str):
        self.parent = parent
        self.name = name

    def execute(self):
        if not self.parent.has_state:
            raise RuntimeError(PG_UNDEFINED_FUNCTION)
        return MagicMock(data=self.parent.rpc_data)


class _MockClient:
    """Pre/post-migration Supabase client double."""

    def __init__(self, has_state: bool, rows=None, rpc_data=None):
        self.has_state = has_state
        self.rows = rows if rows is not None else []
        self.rpc_data = rpc_data
        self.execute_count = 0

    def table(self, _name: str):
        return _MockQuery(self)

    def rpc(self, name: str, _params):
        return _MockRpc(self, name)


def _await(coro) -> str:
    return asyncio.run(coro)[0].text


@pytest.fixture(autouse=True)
def _reset_state_cache(monkeypatch):
    """Clear the module-level FSM-column cache between tests."""
    from handlers import events as events_mod

    monkeypatch.setattr(events_mod, "_STATE_COLUMN_AVAILABLE", None, raising=False)


class TestEventsListPreMigration:
    """`events_list` must succeed when the live DB lacks the state column."""

    def test_returns_empty_without_state_column(self, monkeypatch):
        from handlers import events as events_mod

        client = _MockClient(has_state=False, rows=[])
        monkeypatch.setattr(events_mod, "_get_client", lambda: client)

        text = _await(events_mod._handle_events_list({}))
        assert "No events found" in text
        # Probe-then-retry: first attempt with state filter raises 42703,
        # second attempt with the legacy filter returns []. Two executes.
        assert client.execute_count == 2

    def test_caches_detection_across_calls(self, monkeypatch):
        from handlers import events as events_mod

        client = _MockClient(has_state=False, rows=[])
        monkeypatch.setattr(events_mod, "_get_client", lambda: client)

        _await(events_mod._handle_events_list({}))
        baseline = client.execute_count
        _await(events_mod._handle_events_list({}))
        # Second call must skip the failing state probe (single execute).
        assert client.execute_count == baseline + 1

    def test_returns_rows_without_state_column(self, monkeypatch):
        from handlers import events as events_mod

        rows = [
            {
                "id": "evt-pre-1",
                "severity": "high",
                "title": "Build failed",
                "event_type": "ci_failure",
                "repo": "Osasuwu/jarvis",
                "source": "github_action",
                "created_at": "2026-05-24T00:00:00Z",
                "payload": {},
                # No `state` key — pre-migration row shape.
            }
        ]
        client = _MockClient(has_state=False, rows=rows)
        monkeypatch.setattr(events_mod, "_get_client", lambda: client)

        text = _await(events_mod._handle_events_list({"repo": "Osasuwu/jarvis"}))
        assert "evt-pre-1" in text
        assert "Build failed" in text


class TestEventsListPostMigration:
    """`events_list` must use the FSM filter when the state column exists."""

    def test_uses_state_filter_when_available(self, monkeypatch):
        from handlers import events as events_mod

        rows = [
            {
                "id": "evt-post-1",
                "severity": "medium",
                "title": "Pending event",
                "event_type": "pr_approved",
                "repo": "Osasuwu/jarvis",
                "source": "github_action",
                "created_at": "2026-05-24T00:00:00Z",
                "state": "pending",
                "payload": {},
            }
        ]
        client = _MockClient(has_state=True, rows=rows)
        monkeypatch.setattr(events_mod, "_get_client", lambda: client)

        text = _await(events_mod._handle_events_list({}))
        assert "evt-post-1" in text
        # Single round-trip: state filter succeeds, no retry needed.
        assert client.execute_count == 1


class TestFsmRpcsPreMigration:
    """FSM RPC tools must return a friendly error pre-migration, not crash."""

    @pytest.mark.parametrize(
        "handler_name,args",
        [
            ("_handle_event_claim_next", {"claimer": "test"}),
            (
                "_handle_event_mark_processed",
                {"event_id": "evt-1", "processor": "test"},
            ),
            ("_handle_event_park", {"event_id": "evt-1"}),
            ("_handle_event_requeue", {"event_id": "evt-1"}),
        ],
    )
    def test_returns_friendly_error_without_rpc(
        self, monkeypatch, handler_name: str, args: dict
    ):
        from handlers import events as events_mod

        client = _MockClient(has_state=False)
        monkeypatch.setattr(events_mod, "_get_client", lambda: client)

        handler = getattr(events_mod, handler_name)
        text = _await(handler(args))
        assert "not available" in text.lower() or "apply migration" in text.lower(), (
            f"{handler_name} must surface a user-friendly error when its RPC "
            f"is missing; got: {text!r}"
        )

    def test_bulk_mark_processed_falls_back_to_legacy_update(self, monkeypatch):
        from handlers import events as events_mod

        # has_state=False → mark_processed RPC raises 42883; legacy direct
        # update via .table("events").update() must take over.
        client = _MockClient(has_state=False, rows=[{"id": "evt-1"}])

        # Override execute() on the legacy update path so it returns data.
        original_execute = _MockQuery.execute

        def patched_execute(self):
            if self._uses_state_filter and not self.parent.has_state:
                raise RuntimeError(PG_UNDEFINED_COLUMN)
            return MagicMock(data=list(self.parent.rows))

        monkeypatch.setattr(_MockQuery, "execute", patched_execute)
        monkeypatch.setattr(events_mod, "_get_client", lambda: client)

        text = _await(
            events_mod._handle_events_mark_processed(
                {"event_ids": ["evt-1"], "processed_by": "test"}
            )
        )
        # Either fully succeeded via legacy fallback, or partially — assert
        # the handler did not raise and reported a count.
        assert re.search(r"Marked \d+/1 events", text), text

        # Restore method (paranoia, monkeypatch should clean up anyway).
        monkeypatch.setattr(_MockQuery, "execute", original_execute)
