"""Contract tests for the event queue substrate (#739).

Extends the live events table with state FSM, dedup, NOTIFY trigger, and RPCs.

Two sections:
1. **Migration contract** (static SQL analysis) — asserts the migration file
   declares the correct columns, constraints, trigger, and RPC functions.
2. **MCP handler tests** — asserts the Python handler layer calls the correct
   RPCs and formats responses correctly (mocked Supabase client).
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260521130515_extend_events_queue.sql"
)
SCHEMA_MIRROR = REPO_ROOT / "mcp-memory" / "schema.sql"

# Columns this migration adds to the events table
NEW_COLUMNS = ("state", "dedup_key", "claimed_at", "claimed_by")

# Valid FSM states
VALID_STATES = ("pending", "claimed", "processed", "parked")

# RPC functions this migration declares
RPC_FUNCTIONS = ("claim_next", "mark_processed", "park_event", "requeue_event")

# Indexes this migration adds (in SQL)
MIGRATION_INDEXES = ("idx_events_dedup_key",)
# Additional indexes declared in schema.sql for query performance
SCHEMA_ONLY_INDEXES = ("idx_events_pending",)


# =========================================================================
# Section 1 — Migration contract tests
# =========================================================================


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert MIGRATION.exists(), f"missing migration file: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def schema_sql() -> str:
    assert SCHEMA_MIRROR.exists(), f"missing schema mirror: {SCHEMA_MIRROR}"
    return SCHEMA_MIRROR.read_text(encoding="utf-8")


# -- Columns ----------------------------------------------------------------


@pytest.mark.parametrize("column", NEW_COLUMNS)
def test_migration_adds_column(column: str) -> None:
    """Each new column must appear in the migration."""
    text = MIGRATION.read_text(encoding="utf-8")
    assert re.search(
        rf"ADD COLUMN IF NOT EXISTS\s+{column}\s+", text, re.IGNORECASE
    ), f"column {column!r} missing from migration ADD COLUMN"


@pytest.mark.parametrize("column", NEW_COLUMNS)
def test_schema_mirror_declares_column(column: str) -> None:
    """Each new column must appear in schema.sql events block."""
    block = _extract_events_block(SCHEMA_MIRROR.read_text(encoding="utf-8"))
    assert re.search(
        rf"^\s*{column}\s+", block, re.MULTILINE
    ), f"column {column!r} missing from schema.sql events block"


def test_state_column_has_check_constraint() -> None:
    """State must constrain to the four FSM values."""
    migration = MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"CHECK\s*\(state\s+IN\s*\(([^)]+)\)\)",
        migration,
        re.IGNORECASE,
    )
    assert match, "state column missing CHECK constraint in migration"
    values = [v.strip().strip("'") for v in match.group(1).split(",")]
    for s in VALID_STATES:
        assert s in values, f"valid state {s!r} missing from CHECK constraint; got {values}"


def test_state_column_has_check_in_schema() -> None:
    """schema.sql must also constrain state to the four FSM values."""
    block = _extract_events_block(SCHEMA_MIRROR.read_text(encoding="utf-8"))
    match = re.search(
        r"check\s*\(state\s+in\s*\(([^)]+)\)\)",
        block,
        re.IGNORECASE,
    )
    assert match, "state column missing CHECK constraint in schema.sql"
    values = [v.strip().strip("'") for v in match.group(1).split(",")]
    for s in VALID_STATES:
        assert s in values, f"valid state {s!r} missing from schema.sql CHECK; got {values}"


def test_state_column_defaults_pending() -> None:
    """New rows default to 'pending'."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "DEFAULT 'pending'" in migration, "state must DEFAULT 'pending'"


def test_dedup_key_unique_index() -> None:
    """dedup_key must have a unique partial index (WHERE dedup_key IS NOT NULL)."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "UNIQUE INDEX" in migration, "dedup_key must have UNIQUE index"
    assert "idx_events_dedup_key" in migration, "expected idx_events_dedup_key"
    assert "WHERE dedup_key IS NOT NULL" in migration, (
        "dedup_key unique index must be partial (allow multiple NULLs)"
    )
    schema = SCHEMA_MIRROR.read_text(encoding="utf-8")
    assert "idx_events_dedup_key" in schema, "idx_events_dedup_key missing from schema.sql"


# -- Backfill + start-clean -------------------------------------------------


def test_backfill_legacy_processed() -> None:
    """Migration must backfill legacy processed=true rows into new state."""
    text = MIGRATION.read_text(encoding="utf-8")
    assert "UPDATE events SET state = 'processed' WHERE processed = true" in text or \
           "UPDATE events SET state = 'processed' WHERE processed = true" in text, \
        "backfill of legacy processed=true rows missing"


def test_start_clean_archives_backlog() -> None:
    """Migration must set all existing unprocessed rows to processed (start-clean)."""
    text = MIGRATION.read_text(encoding="utf-8")
    # Look for a second UPDATE that catches remaining pending rows
    updates = re.findall(
        r"UPDATE\s+events\s+SET\s+state\s*=\s*'processed'\s+WHERE\s+state\s*=\s*'pending'",
        text,
        re.IGNORECASE,
    )
    assert len(updates) >= 1, (
        "migration must UPDATE events SET state='processed' WHERE state='pending' "
        "for start-clean"
    )


# -- NOTIFY trigger ---------------------------------------------------------


def test_notify_trigger_present() -> None:
    """Trigger must fire on INSERT into events, mirroring events_canonical pattern."""
    text = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TRIGGER events_notify" in text
    assert "AFTER INSERT ON events" in text
    assert "pg_notify(\n    'events'" in text or (
        "pg_notify(" in text and "'events'" in text
    ), "pg_notify must use 'events' channel"


def test_notify_payload_keys() -> None:
    """Payload must include identifying keys for subscribers."""
    text = MIGRATION.read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "notify_events_insert")
    for key in ("id", "event_type", "severity", "title", "repo"):
        assert f"'{key}'" in fn_block, f"notify payload missing key {key!r}: {fn_block}"


# -- RPC functions ---------------------------------------------------------


@pytest.mark.parametrize("rpc", RPC_FUNCTIONS)
def test_rpc_function_declared_in_migration(rpc: str) -> None:
    """Each RPC function must be declared in the migration."""
    text = MIGRATION.read_text(encoding="utf-8")
    assert f"CREATE OR REPLACE FUNCTION {rpc}" in text, \
        f"RPC function {rpc!r} missing from migration"


@pytest.mark.parametrize("rpc", RPC_FUNCTIONS)
def test_rpc_function_declared_in_schema(rpc: str) -> None:
    """Each RPC function must also be declared in schema.sql."""
    text = SCHEMA_MIRROR.read_text(encoding="utf-8")
    assert f"create or replace function {rpc}" in text, \
        f"RPC function {rpc!r} missing from schema.sql"


def test_claim_next_orders_by_severity_then_age() -> None:
    """claim_next must order critical first, then by created_at ASC."""
    text = MIGRATION.read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "claim_next")
    assert "CASE severity" in fn_block, "claim_next must use CASE for severity ordering"
    assert re.search(r"'critical'\s+THEN\s+0", fn_block), "critical must be priority 0"
    assert re.search(r"'info'\s+THEN\s+4", fn_block), "info must be priority 4"
    assert re.search(r"created_at\s+ASC", fn_block, re.IGNORECASE), "created_at ASC ordering missing"


def test_claim_next_uses_skip_locked() -> None:
    """claim_next must use FOR UPDATE SKIP LOCKED for concurrency safety."""
    text = MIGRATION.read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "claim_next")
    assert "FOR UPDATE SKIP LOCKED" in fn_block.upper() or \
           "for update skip locked" in fn_block, \
        "claim_next must use FOR UPDATE SKIP LOCKED"


@pytest.mark.parametrize("source", ["migration", "schema"])
def test_claim_next_no_ghost_row_on_empty_queue(source: str) -> None:
    """claim_next must not emit an all-NULL ghost row when nothing pending.

    Regression: `RETURN NEXT event_row` placed outside the `IF FOUND` block
    emits an uninitialised rowtype (all NULLs) as a one-row SETOF, which the
    handler cannot distinguish from a real row and reports `Claimed event None`.
    """
    text = (MIGRATION if source == "migration" else SCHEMA_MIRROR).read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "claim_next")
    if_match = re.search(r"\bif\s+found\b", fn_block, re.IGNORECASE)
    end_if_match = re.search(r"\bend\s+if\b", fn_block, re.IGNORECASE)
    return_next_match = re.search(r"\breturn\s+next\s+event_row\b", fn_block, re.IGNORECASE)
    assert if_match and end_if_match and return_next_match, \
        f"claim_next ({source}) is missing IF FOUND / END IF / RETURN NEXT"
    assert if_match.end() < return_next_match.start() < end_if_match.start(), \
        f"claim_next ({source}) emits ghost row — RETURN NEXT must be inside IF FOUND"


def test_mark_processed_requires_claimed_state() -> None:
    """mark_processed must only update rows WHERE state = 'claimed'."""
    text = MIGRATION.read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "mark_processed")
    assert "state = 'claimed'" in fn_block, \
        "mark_processed must guard on state = 'claimed'"


def test_park_event_requires_claimed_state() -> None:
    """park_event must only update rows WHERE state = 'claimed'."""
    text = MIGRATION.read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "park_event")
    assert "state = 'claimed'" in fn_block, \
        "park_event must guard on state = 'claimed'"


def test_requeue_event_allows_claimed_or_parked() -> None:
    """requeue_event must allow both 'claimed' and 'parked' states."""
    text = MIGRATION.read_text(encoding="utf-8")
    fn_block = _extract_function_body(text, "requeue_event")
    assert "state = 'claimed'" in fn_block or "state = 'claimed'" in fn_block
    assert "state = 'parked'" in fn_block or "state = 'parked'" in fn_block
    # Must clear claim metadata on requeue
    assert "claimed_at = null" in fn_block.lower() or \
           "claimed_at = NULL" in fn_block
    assert "claimed_by = null" in fn_block.lower() or \
           "claimed_by = NULL" in fn_block


# -- Indexes ----------------------------------------------------------------


@pytest.mark.parametrize("index", MIGRATION_INDEXES)
def test_migration_declares_index(index: str) -> None:
    assert index in MIGRATION.read_text(encoding="utf-8"), \
        f"index {index!r} missing from migration"


def test_pending_index_exists_in_schema() -> None:
    """schema.sql must also declare the pending-events query index."""
    text = SCHEMA_MIRROR.read_text(encoding="utf-8")
    assert "idx_events_pending" in text, \
        "idx_events_pending missing from schema.sql"
    assert "where state = 'pending'" in text.lower() or \
           "WHERE state = 'pending'" in text, \
        "idx_events_pending must be partial on pending"


# =========================================================================
# Section 2 — MCP handler tests
# =========================================================================


@pytest.fixture
def mock_client() -> MagicMock:
    """Create a mock Supabase client for handler testing."""
    client = MagicMock()
    return client


def _mock_rpc(mock_client: MagicMock, rpc_name: str, return_data: list | bool | None):
    """Set up mock_client.rpc(rpc_name) to return execute()->data."""
    rpc_builder = MagicMock()
    rpc_builder.execute.return_value = MagicMock(data=return_data)
    mock_client.rpc.return_value = rpc_builder


class TestEventClaimNextHandler:
    """Tests for _handle_event_claim_next."""

    def test_claims_next_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_claim_next

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        event = {
            "id": "evt-001",
            "event_type": "ci_failure",
            "severity": "high",
            "title": "Build failed on main",
            "repo": "Osasuwu/jarvis",
        }
        _mock_rpc(client, "claim_next", [event])

        result = await_handler(_handle_event_claim_next({"claimer": "orchestrator"}))

        client.rpc.assert_called_once_with("claim_next", {"claimer": "orchestrator"})
        assert "evt-001" in result
        assert "Build failed on main" in result
        assert "orchestrator" in result

    def test_no_pending_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_claim_next

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "claim_next", None)

        result = await_handler(_handle_event_claim_next({"claimer": "orchestrator"}))
        assert "No pending events" in result


class TestEventMarkProcessedHandler:
    """Tests for _handle_event_mark_processed (FSM variant)."""

    def test_marks_claimed_event_processed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_mark_processed

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "mark_processed", True)

        result = await_handler(
            _handle_event_mark_processed({
                "event_id": "evt-001",
                "processor": "orchestrator",
                "action_taken": "triaged and dispatched",
            })
        )

        client.rpc.assert_called_once_with(
            "mark_processed",
            {"event_id": "evt-001", "processor": "orchestrator", "action_taken": "triaged and dispatched"},
        )
        assert "evt-001" in result
        assert "marked as processed" in result

    def test_rejects_event_not_claimed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_mark_processed

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "mark_processed", False)

        result = await_handler(
            _handle_event_mark_processed({
                "event_id": "evt-001",
                "processor": "orchestrator",
            })
        )

        assert "not in 'claimed' state" in result


class TestEventParkHandler:
    """Tests for _handle_event_park."""

    def test_parks_claimed_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_park

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "park_event", True)

        result = await_handler(
            _handle_event_park({
                "event_id": "evt-001",
                "reason": "waiting for PR merge",
            })
        )

        client.rpc.assert_called_once_with(
            "park_event",
            {"event_id": "evt-001", "reason": "waiting for PR merge"},
        )
        assert "evt-001" in result
        assert "parked" in result

    def test_rejects_event_not_claimed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_park

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "park_event", False)

        result = await_handler(_handle_event_park({"event_id": "evt-001"}))
        assert "not in 'claimed' state" in result


class TestEventRequeueHandler:
    """Tests for _handle_event_requeue."""

    def test_requeues_parked_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_requeue

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "requeue_event", True)

        result = await_handler(
            _handle_event_requeue({
                "event_id": "evt-001",
                "reason": "dependency resolved",
            })
        )

        client.rpc.assert_called_once_with(
            "requeue_event",
            {"event_id": "evt-001", "reason": "dependency resolved"},
        )
        assert "evt-001" in result
        assert "requeued" in result

    def test_rejects_invalid_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.events import _handle_event_requeue

        client = MagicMock()
        monkeypatch.setattr("handlers.events._get_client", lambda: client)

        _mock_rpc(client, "requeue_event", False)

        result = await_handler(_handle_event_requeue({"event_id": "evt-001"}))
        assert "not in 'claimed' or 'parked' state" in result


# =========================================================================
# Helpers
# =========================================================================


def await_handler(coro) -> str:
    """Await an async handler and return the text from its TextContent result."""
    import asyncio
    result = asyncio.run(coro)
    return result[0].text


def _extract_function_body(sql: str, fn_name: str) -> str:
    """Extract the body of a PostgreSQL function from SQL text.

    Handles two styles:
      Style A: LANGUAGE plpgsql AS $$body$$ (RPCs)
      Style B: RETURNS trigger AS $$body$$ LANGUAGE plpgsql (triggers)
    """
    # Find the function definition start
    pattern = rf"CREATE OR REPLACE FUNCTION {re.escape(fn_name)}\(.*?\)\s+"
    match = re.search(pattern, sql, re.IGNORECASE | re.DOTALL)
    assert match, f"function {fn_name!r} not found in migration"
    def_start = match.end()

    # Find the opening $$ — skip past AS if present before it
    rest = sql[def_start:]
    as_match = re.search(r'\$\$', rest)
    assert as_match, f"function {fn_name!r} missing opening $$"
    start = def_start + as_match.end()

    # Find the closing $$
    end = sql.find("$$", start)
    assert end != -1, f"function {fn_name!r} body unterminated"
    return sql[start:end]


def _extract_events_block(sql: str) -> str:
    """Pull the events CREATE TABLE block out of schema.sql, skipping
    events_canonical which has a different shape."""
    # Find the events table definition (the legacy perception table, not
    # events_canonical).
    marker = "create table if not exists events ("
    start = sql.find(marker)
    assert start != -1, "events CREATE TABLE missing from schema.sql"
    # Find the closing ); of the table definition — count parens depth
    depth = 0
    in_block = False
    end = start
    for i, ch in enumerate(sql[start:]):
        if ch == '(':
            depth += 1
            in_block = True
        elif ch == ')':
            depth -= 1
            if in_block and depth == 0:
                end = start + i + 1
                break
    assert end > start, "events block unterminated in schema.sql"
    return sql[start:end]
