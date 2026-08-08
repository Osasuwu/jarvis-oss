"""Contract tests for the C17 events_canonical substrate (#476, Sprint #35).

Verifies the migration file and the schema.sql mirror declare the same
shape that the design 1-pager (docs/design/c17-events-substrate.md) and
the first writer (#477) depend on. Does NOT hit a live database — live
verification was performed via the Supabase MCP `apply_migration` tool
during the implementing session and is reproducible by anyone with the
project ID.

When a future change moves a column or renames an index, these assertions
break loudly so the cascading damage to writers / consumers is caught at
PR time, not in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260429145000_create_events_canonical.sql"
)
SCHEMA_MIRROR = REPO_ROOT / "mcp-memory" / "schema.sql"

REQUIRED_COLUMNS = (
    "event_id",
    "trace_id",
    "parent_event_id",
    "ts",
    "actor",
    "action",
    "payload",
    "outcome",
    "cost_tokens",
    "cost_usd",
    "redacted",
    "degraded",
)

REQUIRED_INDEXES = (
    "idx_events_canonical_trace_ts",
    "idx_events_canonical_actor_ts",
    "idx_events_canonical_action_ts",
    "idx_events_canonical_cost",
)

REQUIRED_MATVIEWS = (
    "events_cost_by_day_mv",
    "events_last_run_by_actor_mv",
)

OTEL_KEYS_IN_VIEW = ("gen_ai.request.model",)


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert MIGRATION.exists(), f"missing migration file: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def schema_sql() -> str:
    assert SCHEMA_MIRROR.exists(), f"missing schema mirror: {SCHEMA_MIRROR}"
    return SCHEMA_MIRROR.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Table shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_migration_declares_column(migration_sql: str, column: str) -> None:
    assert re.search(
        rf"^\s*{column}\s+", migration_sql, re.MULTILINE
    ), f"column {column!r} missing from CREATE TABLE events_canonical"


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_schema_mirror_declares_column(schema_sql: str, column: str) -> None:
    block = _extract_events_canonical_block(schema_sql)
    assert re.search(
        rf"^\s*{column}\s+", block, re.MULTILINE
    ), f"column {column!r} missing from schema.sql events_canonical block"


def test_event_outcome_enum_present(migration_sql: str, schema_sql: str) -> None:
    """outcome column references an event_outcome enum with the four values."""
    for source, label in ((migration_sql, "migration"), (schema_sql, "schema")):
        assert (
            "CREATE TYPE event_outcome AS ENUM" in source
        ), f"event_outcome enum missing from {label}"
        for value in ("success", "failure", "timeout", "partial"):
            assert (
                f"'{value}'" in source
            ), f"enum value {value!r} missing from {label}"


def test_trace_id_is_not_null(migration_sql: str) -> None:
    """trace_id must be NOT NULL — orphaned events break trace replay."""
    line = _line_for_column(migration_sql, "trace_id")
    assert "NOT NULL" in line, f"trace_id must be NOT NULL: {line!r}"


def test_degraded_defaults_false(migration_sql: str) -> None:
    """degraded MUST default to false — only buffer-replay paths set it."""
    line = _line_for_column(migration_sql, "degraded")
    assert "DEFAULT false" in line, f"degraded must DEFAULT false: {line!r}"


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index", REQUIRED_INDEXES)
def test_migration_declares_index(migration_sql: str, index: str) -> None:
    assert (
        index in migration_sql
    ), f"index {index!r} missing from migration"


def test_cost_index_is_partial(migration_sql: str) -> None:
    """The cost index must be partial on cost_usd IS NOT NULL — full index
    on a nullable column would bloat with cost-free events."""
    match = re.search(
        r"CREATE INDEX[^;]*idx_events_canonical_cost[^;]*;",
        migration_sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "idx_events_canonical_cost not found"
    assert "WHERE cost_usd IS NOT NULL" in match.group(
        0
    ), "idx_events_canonical_cost must be partial on cost_usd IS NOT NULL"


# ---------------------------------------------------------------------------
# pg_notify trigger
# ---------------------------------------------------------------------------


def test_pg_notify_trigger_present(migration_sql: str) -> None:
    """Trigger must exist + use channel 'events_canonical' so LISTEN
    clients can subscribe by name."""
    assert "CREATE TRIGGER events_canonical_notify" in migration_sql
    assert "AFTER INSERT ON events_canonical" in migration_sql
    assert "pg_notify(\n    'events_canonical'" in migration_sql or (
        "pg_notify(" in migration_sql and "'events_canonical'" in migration_sql
    )


def test_notify_payload_includes_trace_id(migration_sql: str) -> None:
    """Payload must include trace_id so subscribers can route by trace."""
    fn_block = _extract_function_body(migration_sql, "notify_events_canonical")
    for key in ("event_id", "trace_id", "action", "actor"):
        assert (
            f"'{key}'" in fn_block
        ), f"notify payload missing key {key!r}: {fn_block}"


# ---------------------------------------------------------------------------
# Materialized views + cron schedules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("matview", REQUIRED_MATVIEWS)
def test_migration_declares_matview(migration_sql: str, matview: str) -> None:
    assert (
        f"CREATE MATERIALIZED VIEW IF NOT EXISTS {matview}" in migration_sql
    ), f"matview {matview!r} missing"


@pytest.mark.parametrize("matview", REQUIRED_MATVIEWS)
def test_matview_has_unique_index_for_concurrent_refresh(
    migration_sql: str, matview: str
) -> None:
    """REFRESH MATERIALIZED VIEW CONCURRENTLY requires a unique index."""
    pattern = rf"CREATE UNIQUE INDEX[^;]*ON {matview}"
    assert re.search(
        pattern, migration_sql, re.IGNORECASE
    ), f"matview {matview!r} needs a unique index for CONCURRENTLY refresh"


def test_cost_view_uses_otel_model_key(migration_sql: str) -> None:
    """Cost rollup must read OTel-shaped model key, not a bespoke alias."""
    cost_view = _extract_matview_body(migration_sql, "events_cost_by_day_mv")
    for key in OTEL_KEYS_IN_VIEW:
        assert (
            f"'{key}'" in cost_view
        ), f"cost view must read OTel key {key!r}: {cost_view}"


def test_cost_view_excludes_degraded(migration_sql: str) -> None:
    """Replayed (degraded=true) events must not contribute to cost truth."""
    cost_view = _extract_matview_body(migration_sql, "events_cost_by_day_mv")
    assert (
        "degraded = false" in cost_view
    ), "cost view must filter out degraded=true rows"


def test_pg_cron_schedules_present(migration_sql: str) -> None:
    """Both materialized views need scheduled refreshes."""
    assert (
        "events_cost_by_day_mv_refresh" in migration_sql
    ), "cost view cron job missing"
    assert (
        "events_last_run_by_actor_mv_refresh" in migration_sql
    ), "last_run view cron job missing"


def test_pg_cron_extension_enabled(migration_sql: str) -> None:
    """Migration must enable pg_cron — Supabase ships it but doesn't
    install by default."""
    assert (
        "CREATE EXTENSION IF NOT EXISTS pg_cron" in migration_sql
    ), "migration must enable pg_cron"


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_attr", ["migration_sql", "schema_sql"])
def test_rls_enabled_with_allow_all_policies(
    request: pytest.FixtureRequest, source_attr: str
) -> None:
    """Match existing convention (fok_judgments, task_queue, etc.).

    schema_sql carries the post-#542 split-anon shape (SELECT/UPDATE/DELETE
    open, INSERT gated on actor LIKE 'sandcastle:%'); migration_sql is the
    original create migration which still has the broad `Allow all for anon`.
    """
    source = request.getfixturevalue(source_attr)
    if source_attr == "schema_sql":
        source = _extract_events_canonical_block(source)
    assert (
        "ALTER TABLE events_canonical ENABLE ROW LEVEL SECURITY" in source
    ), f"RLS not enabled in {source_attr}"
    assert (
        '"Allow all for authenticated" ON events_canonical' in source
    ), f"authenticated policy missing in {source_attr}"
    if source_attr == "migration_sql":
        assert (
            '"Allow all for anon" ON events_canonical' in source
        ), "anon policy missing in migration_sql"
    else:
        # post-#542 split-anon shape in schema.sql
        assert (
            '"Anon select" ON events_canonical' in source
        ), "anon SELECT policy missing in schema_sql"
        assert (
            '"Anon sandcastle insert" ON events_canonical' in source
        ), "anon sandcastle INSERT policy missing in schema_sql"
        assert (
            "actor LIKE 'sandcastle:%'" in source
        ), "anon INSERT must be gated on sandcastle: provenance"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_for_column(sql: str, column: str) -> str:
    for line in sql.splitlines():
        if re.match(rf"\s*{column}\s+", line):
            return line.strip()
    raise AssertionError(f"column {column!r} not found")


def _extract_events_canonical_block(schema_sql: str) -> str:
    """Pull the events_canonical CREATE TABLE block out of schema.sql so
    column lookups don't accidentally match the legacy `events` table."""
    marker = "CREATE TABLE IF NOT EXISTS events_canonical"
    start = schema_sql.find(marker)
    assert start != -1, "events_canonical CREATE TABLE missing from schema.sql"
    end = schema_sql.find(");", start)
    assert end != -1, "events_canonical block missing closing );"
    # Include everything from the CREATE TABLE start through the rest of
    # the file — the block extends to RLS + matviews and tests look for
    # those further down.
    return schema_sql[start:]


def _extract_function_body(sql: str, fn_name: str) -> str:
    pattern = rf"CREATE OR REPLACE FUNCTION {fn_name}\(\)\s+RETURNS trigger.*?\$\$"
    match = re.search(pattern, sql, re.DOTALL)
    assert match, f"function {fn_name!r} not found"
    start = match.end()
    end = sql.find("$$", start)
    assert end != -1, f"function {fn_name!r} body unterminated"
    return sql[start:end]


def _extract_matview_body(sql: str, matview: str) -> str:
    pattern = rf"CREATE MATERIALIZED VIEW IF NOT EXISTS {matview} AS"
    start = sql.find(pattern)
    if start == -1:
        # Fall back to a less strict prefix for the schema mirror style.
        start = sql.find(f"MATERIALIZED VIEW IF NOT EXISTS {matview}")
    assert start != -1, f"matview {matview!r} not found"
    end = sql.find("WITH NO DATA", start)
    if end == -1:
        end = sql.find(";", start)
    assert end != -1, f"matview {matview!r} body unterminated"
    return sql[start:end]
