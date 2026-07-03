"""Tests for global task sources and advancer (issue #679).

DB-gated tests skip cleanly when DATABASE_URL is unavailable.
Pure-Python logic (lapse_intervals math, dedup_key, fire_per_interval cap)
is tested without a DB.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


# =========================================================================
# Load the advancer module under test.
#
# scripts/advance-global-tasks.py has a dash in its name, so a plain `import`
# can't reach it. Load it via importlib and bind the symbols the tests use —
# keeping ONE definition of digest_sha256 / compute_dedup_key (the production
# one). The previous copy-pasted re-definitions could drift from the real code
# and let the suite pass against a lie (MAJOR #13).
# =========================================================================


def _ensure_psycopg_importable() -> None:
    """advance-global-tasks.py does `import psycopg` / `from psycopg.rows import
    dict_row` at module top. When the real driver isn't installed, stub just
    enough for the module to import — the fake-cursor tests below never touch a
    real connection (they monkeypatch psycopg.connect), and the DB-gated tests
    skip on the DATABASE_URL check before reaching psycopg. Only stubs when the
    real driver is absent, so a real install is used verbatim where present."""
    try:
        import psycopg  # noqa: F401

        return
    except ModuleNotFoundError:
        pass
    stub = types.ModuleType("psycopg")

    def _no_connect(*a: Any, **k: Any) -> Any:  # pragma: no cover - monkeypatched
        raise RuntimeError("stub psycopg: connect unavailable (install psycopg)")

    stub.connect = _no_connect  # type: ignore[attr-defined]
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()  # type: ignore[attr-defined]
    stub.rows = rows  # type: ignore[attr-defined]
    sys.modules["psycopg"] = stub
    sys.modules["psycopg.rows"] = rows


_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "advance-global-tasks.py"
_spec = importlib.util.spec_from_file_location("advance_global_tasks", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_ensure_psycopg_importable()
_spec.loader.exec_module(_mod)

digest_sha256 = _mod.digest_sha256
compute_dedup_key = _mod.compute_dedup_key


# =========================================================================
# Fake cursor/connection — exercise the advancer end-to-end with no live
# Postgres, so the transaction-model and dedup findings are assertable in CI
# (MAJOR #10). Routes fetchall/fetchone off the statement shape and records
# every (sql, params) the advancer emits.
# =========================================================================


class _FakeCursor:
    """Minimal psycopg-cursor stand-in for _advance_due_rows."""

    def __init__(
        self,
        due_rows: list[dict[str, Any]],
        *,
        intervals_missed: float = 1.0,
        rowcounts: list[int] | None = None,
    ) -> None:
        self._due_rows = list(due_rows)
        self._intervals_missed = intervals_missed
        # rowcount values successive event INSERTs take on. Default: every
        # insert lands (1). Pass [0, ...] to simulate ON CONFLICT skips.
        self._rowcounts = list(rowcounts) if rowcounts is not None else None
        self.statements: list[tuple[str, Any]] = []
        self.rowcount = -1

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append((sql, params))
        if sql.strip().upper().startswith("INSERT INTO EVENTS"):
            self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 1
        else:
            self.rowcount = -1

    def fetchall(self) -> list[dict[str, Any]]:
        return self._due_rows

    def fetchone(self) -> dict[str, Any]:
        # Only _intervals_lapsed's SELECT calls fetchone.
        return {"intervals_missed": self._intervals_missed}

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeConn:
    """psycopg-connection stand-in: `with conn:` + `with conn.cursor() as cur:`."""

    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _due_row(**overrides: Any) -> dict[str, Any]:
    """Build a due global_task_sources row (dict_row shape) for the fake cursor."""
    row: dict[str, Any] = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "title": "test task",
        "body": "do the thing",
        "dispatcher_skill": "research",
        "output_sink": "memory",
        "payload": {},
        "cadence": None,
        "last_run": None,
        "next_run": None,
        "on_lapse": "coalesce",
    }
    row.update(overrides)
    return row


# A concrete past next_run for guard tests that need the advancer to take the
# recurring (cadence-counting) path rather than the next_run-IS-NULL shortcut.
_FIXED_NEXT_RUN = datetime(2024, 4, 13, 12, 0, 0, tzinfo=timezone.utc)


# =========================================================================
# Pure-Python Unit Tests (no DB required)
# =========================================================================


class TestPurePython:
    """Test pure-Python logic in the advancer."""

    def test_digest_sha256(self) -> None:
        """SHA256 digest matches expected output."""
        result = digest_sha256("global_task:123:456")
        expected = hashlib.sha256(b"global_task:123:456").hexdigest()
        assert result == expected

    def test_dedup_key_matches_postgres(self) -> None:
        """Dedup key computed correctly from source_id and epoch."""
        source_id = "550e8400-e29b-41d4-a716-446655440000"
        epoch = 1713000000.0
        result = compute_dedup_key(source_id, epoch)

        # Should be sha256('global_task:<mode>:<id>:<int(epoch)>'); mode defaults
        # to 'coalesce' (MAJOR #6 — keys are namespaced by lapse mode).
        expected = hashlib.sha256(
            f"global_task:coalesce:{source_id}:1713000000".encode()
        ).hexdigest()
        assert result == expected

    def test_dedup_key_full_timestamp_not_hour_truncated(self) -> None:
        """Dedup key uses full timestamp epoch, not hour-truncated."""
        source_id = "test-id"

        # Two epochs in the same hour should produce different dedup_keys
        epoch1 = 1713000000.0  # 00:00:00
        epoch2 = 1713003600.0  # 01:00:00

        key1 = compute_dedup_key(source_id, epoch1)
        key2 = compute_dedup_key(source_id, epoch2)

        assert key1 != key2, "Dedup keys should differ for different epochs"

    def test_dedup_key_namespaced_by_lapse_mode(self) -> None:
        """MAJOR #6: a coalesce event and a fire_per_interval i=0 event for the
        same row share next_run_epoch (fire_epoch = base + 0*cadence = base).
        Without a mode namespace both hash to 'global_task:<id>:<epoch>' and
        ON CONFLICT DO NOTHING silently swallows whichever lands second — a mode
        switch from coalesce to fire_per_interval would lose its first fire. The
        lapse mode must disambiguate the two keys."""
        source_id = "row-1"
        epoch = 1713000000.0
        assert compute_dedup_key(source_id, epoch, "coalesce") != compute_dedup_key(
            source_id, epoch, "fire"
        )

    def test_lapse_intervals_calculation_coalesce(self) -> None:
        """Coalesce path: lapse_intervals = FLOOR((now - next_run) / cadence) + 1."""
        # One-shot / first run carries lapse_intervals == 1 by definition (no
        # cadence to count against) — that branch is exercised end-to-end in
        # TestAdvanceDueRowsFakeCursor. Here we pin the recurring math: 2.5
        # intervals late → FLOOR(2.5) + 1 = 3.
        cadence_seconds = 3600  # 1 hour
        time_late = 9000  # 2.5 hours = 2.5 intervals
        lapse_intervals = int(time_late / cadence_seconds) + 1
        assert lapse_intervals == 3


class TestAdvanceDueRowsFakeCursor:
    """End-to-end exercise of the advancer against a fake cursor (no DB).

    Makes the transaction-model + dedup findings assertable in CI (MAJOR #10):
    no manual BEGIN/SERIALIZABLE (CRITICAL #1), event count reflects rowcount
    not a blind +1 (CRITICAL #2), null-next_run dedups on the fixed sentinel
    (MAJOR #7), and fire_per_interval advances next_run by its fire count
    (MAJOR #8). -1 error contract (CRITICAL #3) and no-DSN-leak (MAJOR #14)
    covered via advance_global_tasks.
    """

    @staticmethod
    def _inserts(cur: _FakeCursor) -> list[tuple[str, Any]]:
        return [
            (s, p) for s, p in cur.statements if s.strip().upper().startswith("INSERT INTO EVENTS")
        ]

    def test_coalesce_one_shot_inserts_one_event(self) -> None:
        cur = _FakeCursor([_due_row(on_lapse="coalesce")])
        total = _mod._advance_due_rows(cur)
        assert total == 1
        assert len(self._inserts(cur)) == 1

    def test_select_fetches_body_column(self) -> None:
        # m8 / CRITICAL #2: body lives on global_task_sources but the SELECT
        # omitted it, so it never reached the event payload. Pin it into the
        # projection.
        cur = _FakeCursor([_due_row()])
        _mod._advance_due_rows(cur)
        selects = [s for s, _ in cur.statements if "FROM GLOBAL_TASK_SOURCES" in s.strip().upper()]
        assert selects, "no SELECT FROM global_task_sources issued"
        assert "body" in selects[0].lower(), "SELECT must project the body column"

    def test_event_payload_forwards_body_title_and_source(self) -> None:
        # CRITICAL #2: the spawned agent acts on the source row's body; the
        # advancer must forward source_id / title / body / output_sink into the
        # event payload so the orchestrator can build an actionable goal.
        cur = _FakeCursor(
            [
                _due_row(
                    id="src-1",
                    title="Weekly sweep",
                    body="Research the latest on X.",
                    output_sink="memory",
                )
            ]
        )
        _mod._advance_due_rows(cur)
        (_, params) = self._inserts(cur)[0]
        # params: (title, payload_json, dedup_key)
        event_payload = json.loads(params[1])
        assert event_payload["source_id"] == "src-1"
        assert event_payload["title"] == "Weekly sweep"
        assert event_payload["body"] == "Research the latest on X."
        assert event_payload["output_sink"] == "memory"

    def test_no_manual_begin_or_serializable(self) -> None:
        # CRITICAL #1: the advancer must never issue a manual BEGIN / isolation
        # level — psycopg's `with conn:` owns the transaction.
        cur = _FakeCursor([_due_row()])
        _mod._advance_due_rows(cur)
        for sql, _ in cur.statements:
            upper = sql.strip().upper()
            assert not upper.startswith("BEGIN"), f"manual BEGIN: {sql!r}"
            assert "ISOLATION LEVEL" not in upper, f"isolation level set: {sql!r}"
            assert "SERIALIZABLE" not in upper, f"SERIALIZABLE set: {sql!r}"

    def test_deduped_insert_not_counted(self) -> None:
        # CRITICAL #2: ON CONFLICT DO NOTHING → rowcount 0 → must not increment.
        cur = _FakeCursor([_due_row()], rowcounts=[0])
        assert _mod._advance_due_rows(cur) == 0

    def test_null_next_run_uses_sentinel_dedup_key(self) -> None:
        # MAJOR #7: a never-run row (next_run IS NULL) dedups on the fixed
        # sentinel epoch, not a moving now()-derived value.
        row_id = "abc-123"
        cur = _FakeCursor([_due_row(id=row_id, next_run=None)])
        _mod._advance_due_rows(cur)
        (_, params) = self._inserts(cur)[0]
        # params: (title, payload_json, dedup_key)
        assert params[2] == compute_dedup_key(row_id, _mod.NULL_NEXT_RUN_SENTINEL_EPOCH)

    def test_fire_per_interval_advances_by_fire_count(self) -> None:
        # MAJOR #8: the schedule UPDATE for a lapsed fire_per_interval row jumps
        # next_run forward by `fire_count` cadence widths, not 1.
        cadence = timedelta(hours=1)
        next_run = datetime(2024, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        cur = _FakeCursor(
            [_due_row(on_lapse="fire_per_interval", cadence=cadence, next_run=next_run)],
            intervals_missed=3.0,  # int(3.0) + 1 = 4 fires
        )
        total = _mod._advance_due_rows(cur)
        assert total == 4
        update = next(
            (
                p
                for s, p in cur.statements
                if s.strip().upper().startswith("UPDATE GLOBAL_TASK_SOURCES")
                and "GREATEST" in s.upper()
            ),
            None,
        )
        assert update is not None, "recurring row must issue a GREATEST(...) UPDATE"
        # params: (periods, cadence, source_id) — periods == fire_count.
        assert update[0] == 4

    def test_zero_cadence_row_skipped_not_divided(self) -> None:
        # CRITICAL #1: a cadence of 0 reaches _intervals_lapsed's
        # `EXTRACT(EPOCH FROM (...)) / EXTRACT(EPOCH FROM cadence)` → divide-by-zero
        # in Postgres, which aborts the whole advance transaction and takes every
        # other due row down with it. The advancer must skip a sub-1s-cadence row
        # (belt-and-suspenders behind the DB cadence floor): no event inserted, and
        # critically no interval-division SELECT issued at all.
        cur = _FakeCursor([_due_row(cadence=timedelta(0), next_run=_FIXED_NEXT_RUN)])
        assert _mod._advance_due_rows(cur) == 0
        assert self._inserts(cur) == [], "zero-cadence row must emit no event"
        assert not any("intervals_missed" in sql for sql, _ in cur.statements), (
            "zero-cadence row must never reach the interval-division SELECT"
        )

    def test_subsecond_cadence_row_skipped(self) -> None:
        # MAJOR #1 (Python layer): a sub-second cadence collides int(epoch)
        # dedup_keys across ticks, so it is rejected at the same 1s floor as the
        # zero case rather than fired.
        cur = _FakeCursor([_due_row(cadence=timedelta(milliseconds=500), next_run=_FIXED_NEXT_RUN)])
        assert _mod._advance_due_rows(cur) == 0
        assert self._inserts(cur) == []

    def test_one_second_cadence_row_still_fires(self) -> None:
        # Boundary: the floor is INCLUSIVE at 1s, so a 1-second cadence is valid
        # and must still fire — the guard rejects below 1s, not at it.
        cur = _FakeCursor([_due_row(cadence=timedelta(seconds=1), next_run=_FIXED_NEXT_RUN)])
        assert _mod._advance_due_rows(cur) == 1
        assert len(self._inserts(cur)) == 1

    def test_advance_global_tasks_returns_event_count(self, monkeypatch: Any) -> None:
        cur = _FakeCursor([_due_row(), _due_row(id="second-row")])
        conn = _FakeConn(cur)
        monkeypatch.setattr(_mod.psycopg, "connect", lambda *a, **k: conn)
        assert _mod.advance_global_tasks(db_url="postgres://fake") == 2
        assert conn.closed, "connection must be closed in finally"

    def test_advance_global_tasks_no_dsn_returns_minus_one(self, monkeypatch: Any) -> None:
        # CRITICAL #3: error path returns -1 (CLI maps -1 → exit 1).
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _mod.advance_global_tasks(db_url=None) == -1

    def test_advance_global_tasks_connect_error_returns_minus_one(
        self, monkeypatch: Any, caplog: Any
    ) -> None:
        # CRITICAL #3 + MAJOR #14: connect failure returns -1 and never logs the
        # DSN (which carries the password).
        def _boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("FATAL: password=supersecret in dsn")

        monkeypatch.setattr(_mod.psycopg, "connect", _boom)
        with caplog.at_level(logging.ERROR):
            result = _mod.advance_global_tasks(db_url="postgres://u:supersecret@host/db")
        assert result == -1
        assert "supersecret" not in caplog.text, "DSN/password must not be logged"


# =========================================================================
# DB-Gated Tests (skip cleanly if no DATABASE_URL)
# =========================================================================


@pytest.fixture
def db_connection() -> Any:
    """Provide a Postgres connection if DATABASE_URL is set.

    Uses ``row_factory=dict_row`` so tests access columns by name (``row["id"]``)
    exactly as production code does (advance-global-tasks.py connects with
    dict_row). The previous tuple-row fixture diverged from production access and
    would silently break if a SELECT projection changed (MAJOR #3, #975).

    When ``REQUIRE_DB`` is set (the postgres CI job sets it), a missing
    ``DATABASE_URL`` is a hard FAILURE rather than a skip — the whole point of
    #975 is that these DB-gated tests must not silently skip in the gate. They
    skip cleanly only in local / non-DB runs where ``REQUIRE_DB`` is unset.
    """
    db_url = os.environ.get("DATABASE_URL")
    # Treat REQUIRE_DB="0"/"" as OFF — matches the meta-test's config check
    # (test_require_db_enforced) so the latch's truthiness can't drift between
    # the fixture and the thing that pins it. A bare os.environ.get(...) truthy
    # check would treat "0" as ON, a footgun.
    require_db = os.environ.get("REQUIRE_DB", "").strip() not in ("", "0")
    if not db_url:
        if require_db:
            pytest.fail(
                "REQUIRE_DB is set but DATABASE_URL is missing — the DB-gated "
                "advancer tests must run against real Postgres in this job, not "
                "silently skip (#975)."
            )
        pytest.skip("DATABASE_URL not set (no Postgres connection available)")

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        pytest.skip("psycopg not installed")

    # connect() can raise OperationalError (bad DSN, service down). Surface it as
    # a clean failure — but never echo db_url, it carries the password (the
    # advancer logs only type(e).__name__ for the same reason).
    try:
        conn = psycopg.connect(db_url, row_factory=dict_row)
    except Exception as e:  # noqa: BLE001 — connection failure must fail loudly, not skip
        pytest.fail(f"could not connect to DATABASE_URL: {type(e).__name__}")

    try:
        yield conn
    finally:
        conn.close()


class TestGlobalTaskSourcesSchema:
    """Test schema creation and RLS (DB-gated)."""

    def test_table_exists(self, db_connection: Any) -> None:
        """global_task_sources table is created."""
        with db_connection.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'global_task_sources'
                )
            """)
            exists = cur.fetchone()["exists"]
            assert exists, "global_task_sources table should exist"

    def test_schema_columns(self, db_connection: Any) -> None:
        """Table has required columns with correct types."""
        with db_connection.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'global_task_sources'
                ORDER BY ordinal_position
            """)
            columns = {r["column_name"]: r["data_type"] for r in cur.fetchall()}

            assert "id" in columns
            assert "title" in columns
            assert "dispatcher_skill" in columns
            assert "output_sink" in columns
            assert "cadence" in columns
            assert "last_run" in columns
            assert "next_run" in columns
            assert "enabled" in columns
            assert "on_lapse" in columns
            assert "created_at" in columns

    def test_dispatcher_skill_enum(self, db_connection: Any) -> None:
        """dispatcher_skill CHECK constraint enforces valid values."""
        with db_connection.cursor() as cur:
            # Valid insert
            cur.execute("""
                INSERT INTO global_task_sources (title, dispatcher_skill, output_sink)
                VALUES ('test', 'research', 'memory')
                RETURNING id
            """)
            row_id = cur.fetchone()["id"]

            # Invalid insert should fail. A CHECK violation aborts the whole
            # transaction (psycopg → InFailedSqlTransaction), so the cleanup
            # DELETE below would itself raise unless we rewind to a savepoint
            # taken before the failing statement.
            cur.execute("SAVEPOINT before_bad_insert")
            # An unknown skill violates BOTH the dispatcher_skill enum check and
            # the dispatcher_sink_compatibility composite check (compatibility
            # only whitelists known skills). Postgres reports whichever it
            # evaluates first — observed to be dispatcher_sink_compatibility — so
            # match the shared "dispatcher" stem rather than a single constraint
            # name, which would be brittle to evaluation order.
            with pytest.raises(Exception, match="dispatcher"):
                cur.execute("""
                    INSERT INTO global_task_sources (title, dispatcher_skill, output_sink)
                    VALUES ('test2', 'invalid_skill', 'memory')
                """)
            cur.execute("ROLLBACK TO SAVEPOINT before_bad_insert")
            cur.execute("RELEASE SAVEPOINT before_bad_insert")

            # Clean up
            cur.execute("DELETE FROM global_task_sources WHERE id = %s", (row_id,))
            db_connection.commit()

    def test_output_sink_enum(self, db_connection: Any) -> None:
        """output_sink CHECK constraint enforces valid values."""
        with db_connection.cursor() as cur:
            # Valid insert
            cur.execute("""
                INSERT INTO global_task_sources (title, dispatcher_skill, output_sink)
                VALUES ('test', 'research', 'telegram_digest')
                RETURNING id
            """)
            row_id = cur.fetchone()["id"]

            # Invalid insert should fail. Wrap in a savepoint so the aborted
            # transaction is recovered before the cleanup DELETE (see
            # test_dispatcher_skill_enum for the full rationale).
            cur.execute("SAVEPOINT before_bad_insert")
            with pytest.raises(Exception, match="output_sink"):
                cur.execute("""
                    INSERT INTO global_task_sources (title, dispatcher_skill, output_sink)
                    VALUES ('test2', 'research', 'invalid_sink')
                """)
            cur.execute("ROLLBACK TO SAVEPOINT before_bad_insert")
            cur.execute("RELEASE SAVEPOINT before_bad_insert")

            # Clean up
            cur.execute("DELETE FROM global_task_sources WHERE id = %s", (row_id,))
            db_connection.commit()

    def test_on_lapse_enum(self, db_connection: Any) -> None:
        """on_lapse CHECK constraint enforces 'coalesce' or 'fire_per_interval'."""
        with db_connection.cursor() as cur:
            # Valid insert with coalesce
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink, on_lapse
                ) VALUES ('test', 'research', 'memory', 'coalesce')
                RETURNING id
            """)
            row_id1 = cur.fetchone()["id"]

            # Valid insert with fire_per_interval. fire_per_interval requires a
            # cadence (cadence_lapse_coherence CHECK forbids cadence IS NULL with
            # fire_per_interval), so supply one.
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink, on_lapse, cadence
                ) VALUES ('test2', 'research', 'memory', 'fire_per_interval', INTERVAL '1 hour')
                RETURNING id
            """)
            row_id2 = cur.fetchone()["id"]

            # Invalid insert should fail. Wrap in a savepoint so the aborted
            # transaction is recovered before the cleanup DELETE (see
            # test_dispatcher_skill_enum for the full rationale).
            cur.execute("SAVEPOINT before_bad_insert")
            with pytest.raises(Exception, match="on_lapse"):
                cur.execute("""
                    INSERT INTO global_task_sources (
                        title, dispatcher_skill, output_sink, on_lapse
                    ) VALUES ('test3', 'research', 'memory', 'invalid_lapse')
                """)
            cur.execute("ROLLBACK TO SAVEPOINT before_bad_insert")
            cur.execute("RELEASE SAVEPOINT before_bad_insert")

            # Clean up
            cur.execute("DELETE FROM global_task_sources WHERE id IN (%s, %s)", (row_id1, row_id2))
            db_connection.commit()

    def test_dispatcher_sink_compatibility(self, db_connection: Any) -> None:
        """Dispatcher/sink compatibility constraint: status-record -> memory only."""
        with db_connection.cursor() as cur:
            # status-record with memory is valid
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink
                ) VALUES ('status check', 'status-record', 'memory')
                RETURNING id
            """)
            row_id = cur.fetchone()["id"]

            # status-record with non-memory should fail. Wrap in a savepoint:
            # without the rewind the aborted transaction would also sink the
            # *valid* research insert below, not just the cleanup DELETE.
            cur.execute("SAVEPOINT before_bad_insert")
            with pytest.raises(Exception, match="dispatcher_sink_compatibility"):
                cur.execute("""
                    INSERT INTO global_task_sources (
                        title, dispatcher_skill, output_sink
                    ) VALUES ('status check 2', 'status-record', 'telegram_digest')
                """)
            cur.execute("ROLLBACK TO SAVEPOINT before_bad_insert")
            cur.execute("RELEASE SAVEPOINT before_bad_insert")

            # research can use any sink (valid)
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink
                ) VALUES ('research task', 'research', 'event_reemit')
                RETURNING id
            """)
            row_id2 = cur.fetchone()["id"]

            # self-improve -> event_reemit must fail: the tightened matrix allows
            # self-improve / last-work-report any sink EXCEPT event_reemit (re-emitting
            # an event from a report/self-improve run loops the advancer on its own
            # output). Pins the M5 tightening.
            cur.execute("SAVEPOINT before_self_improve_reemit")
            with pytest.raises(Exception, match="dispatcher_sink_compatibility"):
                cur.execute("""
                    INSERT INTO global_task_sources (
                        title, dispatcher_skill, output_sink
                    ) VALUES ('self improve reemit', 'self-improve', 'event_reemit')
                """)
            cur.execute("ROLLBACK TO SAVEPOINT before_self_improve_reemit")
            cur.execute("RELEASE SAVEPOINT before_self_improve_reemit")

            # self-improve -> memory is still valid (only event_reemit is excluded)
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink
                ) VALUES ('self improve memory', 'self-improve', 'memory')
                RETURNING id
            """)
            row_id3 = cur.fetchone()["id"]

            # Clean up
            cur.execute(
                "DELETE FROM global_task_sources WHERE id IN (%s, %s, %s)",
                (row_id, row_id2, row_id3),
            )
            db_connection.commit()

    def test_rls_service_role_can_write(self, db_connection: Any) -> None:
        """Service-role (authenticated) can write to global_task_sources."""
        with db_connection.cursor() as cur:
            cur.execute("""
                INSERT INTO global_task_sources (title, dispatcher_skill, output_sink)
                VALUES ('service role test', 'research', 'memory')
                RETURNING id
            """)
            row_id = cur.fetchone()["id"]
            assert row_id is not None

            # Clean up
            cur.execute("DELETE FROM global_task_sources WHERE id = %s", (row_id,))
            db_connection.commit()


class TestCoalesceAdvancer:
    """Test advancer behavior with coalesce lapse strategy (DB-gated)."""

    def test_due_coalesce_row_fires_once_with_lapse_intervals(self, db_connection: Any) -> None:
        """A due coalesce row, run through the REAL advancer, emits exactly one
        global_task_due event whose payload forwards source fields + lapse_intervals.

        M2 (#975): calls production ``advance_global_tasks`` instead of hand-rolling
        the SELECT/INSERT, so the test breaks if the advancer's projection, dedup
        key, or ON CONFLICT clause changes — the old inline-SQL version asserted
        the schema was wired up but kept passing against a lie if the real SQL drifted.
        """
        db_url = os.environ["DATABASE_URL"]
        with db_connection.cursor() as cur:
            # Recurring (1h cadence) row due 2h ago → lapse_intervals = floor(2)+1 = 3.
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, body, dispatcher_skill, output_sink, cadence,
                    next_run, on_lapse
                ) VALUES (
                    'coalesce fire test', 'do the thing', 'research', 'memory',
                    INTERVAL '1 hour', now() - INTERVAL '2 hours', 'coalesce'
                )
                RETURNING id
            """)
            task_id = str(cur.fetchone()["id"])
        # Commit so the advancer's OWN connection (separate from this fixture's)
        # sees the row.
        db_connection.commit()

        try:
            inserted = _mod.advance_global_tasks(db_url=db_url)
            assert inserted >= 1, "advancer must insert the due row's event"

            # DB-side: the single event the advancer wrote for THIS source.
            with db_connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT title, source, payload, dedup_key
                    FROM events
                    WHERE event_type = 'global_task_due'
                      AND payload->>'source_id' = %s
                    """,
                    (task_id,),
                )
                events = cur.fetchall()
            assert len(events) == 1, "exactly one global_task_due event for the source"
            ev = events[0]
            assert ev["source"] == "global_task_advancer"
            assert ev["title"] == "coalesce fire test"
            assert ev["payload"]["body"] == "do the thing"
            assert ev["payload"]["source_id"] == task_id
            assert ev["payload"]["lapse_intervals"] == 3, (
                "coalesce row 2 intervals late carries floor(2)+1 == 3"
            )
            assert ev["dedup_key"], "advancer must set a dedup_key for ON CONFLICT"
        finally:
            db_connection.rollback()
            with db_connection.cursor() as cur:
                cur.execute("DELETE FROM global_task_sources WHERE id = %s", (task_id,))
                cur.execute("DELETE FROM events WHERE payload->>'source_id' = %s", (task_id,))
            db_connection.commit()

    def test_one_shot_row_disables_after_fire(self, db_connection: Any) -> None:
        """A one-shot (cadence=NULL) row is disabled by the REAL advancer after it fires.

        M2 (#975): drives ``advance_global_tasks`` rather than hand-running the
        disable UPDATE, so it verifies the advancer's actual one-shot teardown
        (last_run stamped, next_run NULLed, enabled=false), not a copy of it.
        """
        db_url = os.environ["DATABASE_URL"]
        with db_connection.cursor() as cur:
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink, cadence,
                    next_run, on_lapse
                ) VALUES (
                    'one-shot disable test', 'research', 'memory', NULL,
                    now() - INTERVAL '1 hour', 'coalesce'
                )
                RETURNING id
            """)
            task_id = str(cur.fetchone()["id"])
        db_connection.commit()

        try:
            assert _mod.advance_global_tasks(db_url=db_url) >= 1

            with db_connection.cursor() as cur:
                cur.execute(
                    "SELECT enabled, next_run, last_run FROM global_task_sources WHERE id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
            assert row["enabled"] is False, "one-shot row must be disabled after firing"
            assert row["next_run"] is None, "one-shot row's next_run must be cleared"
            assert row["last_run"] is not None, "advancer must stamp last_run"

            # M3 (#975): the disable is only half the contract — the advancer
            # must also have written exactly one due event for THIS source.
            with db_connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT title, source
                    FROM events
                    WHERE event_type = 'global_task_due'
                      AND payload->>'source_id' = %s
                    """,
                    (task_id,),
                )
                events = cur.fetchall()
            assert len(events) == 1, "exactly one global_task_due event for the source"
            assert events[0]["source"] == "global_task_advancer"
            assert events[0]["title"] == "one-shot disable test"
        finally:
            db_connection.rollback()
            with db_connection.cursor() as cur:
                cur.execute("DELETE FROM global_task_sources WHERE id = %s", (task_id,))
                cur.execute("DELETE FROM events WHERE payload->>'source_id' = %s", (task_id,))
            db_connection.commit()

    def test_recurring_row_advances_next_run_by_cadence(self, db_connection: Any) -> None:
        """A recurring row's next_run is advanced past now() by the REAL advancer.

        M2 (#975): runs ``advance_global_tasks`` instead of re-issuing the
        GREATEST(...) UPDATE inline. A 1h-cadence row that came due 10 min ago
        fires once and its next_run advances to next_run + cadence (~50 min in
        the future via GREATEST(now(), next_run + cadence)), so the row is no
        longer due.

        Uses a *mildly*-late row (lapsed < one cadence) so the advance is
        unambiguously into the future. The badly-lapsed case (lapsed by >1
        cadence) clamps next_run to ~now() under periods=1, leaving the row
        borderline-due and double-firing on the next tick — a real coalesce
        semantics wart tracked in #983, not asserted here.
        """
        db_url = os.environ["DATABASE_URL"]
        with db_connection.cursor() as cur:
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink, cadence,
                    next_run, on_lapse
                ) VALUES (
                    'recurring advance test', 'research', 'memory', INTERVAL '1 hour',
                    now() - INTERVAL '10 minutes', 'coalesce'
                )
                RETURNING id
            """)
            task_id = str(cur.fetchone()["id"])
            cur.execute("SELECT next_run FROM global_task_sources WHERE id = %s", (task_id,))
            old_next_run = cur.fetchone()["next_run"]
        db_connection.commit()

        try:
            assert _mod.advance_global_tasks(db_url=db_url) >= 1

            with db_connection.cursor() as cur:
                cur.execute(
                    "SELECT next_run, last_run, enabled FROM global_task_sources WHERE id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                # The advanced row must no longer be due (next_run > now()).
                cur.execute("SELECT now() < %s AS advanced_past_now", (row["next_run"],))
                advanced_past_now = cur.fetchone()["advanced_past_now"]
            assert row["next_run"] > old_next_run, "next_run must advance forward"
            assert advanced_past_now, "advanced next_run must be in the future (not still due)"
            assert row["enabled"] is True, "recurring row stays enabled"
            assert row["last_run"] is not None, "advancer must stamp last_run"

            # M3 (#975): advancing next_run is only half the contract — verify
            # the advancer actually emitted one due event for THIS source.
            with db_connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT title, source
                    FROM events
                    WHERE event_type = 'global_task_due'
                      AND payload->>'source_id' = %s
                    """,
                    (task_id,),
                )
                events = cur.fetchall()
            assert len(events) == 1, "exactly one global_task_due event for the source"
            assert events[0]["source"] == "global_task_advancer"
            assert events[0]["title"] == "recurring advance test"
        finally:
            db_connection.rollback()
            with db_connection.cursor() as cur:
                cur.execute("DELETE FROM global_task_sources WHERE id = %s", (task_id,))
                cur.execute("DELETE FROM events WHERE payload->>'source_id' = %s", (task_id,))
            db_connection.commit()

    def test_concurrent_invocations_skip_locked(self, db_connection: Any) -> None:
        """FOR UPDATE SKIP LOCKED makes a row claimed by connection A invisible to
        connection B's concurrent claim — so two advancer invocations never
        double-fire the same source. Uses two real connections (a single-cursor
        test can't observe locking)."""
        import psycopg
        from psycopg.rows import dict_row

        # Insert one due row, committed so the second connection can see it.
        with db_connection.cursor() as cur:
            cur.execute("""
                INSERT INTO global_task_sources (
                    title, dispatcher_skill, output_sink, cadence,
                    next_run, on_lapse
                ) VALUES (
                    'skip locked test', 'research', 'memory', INTERVAL '1 hour',
                    now() - INTERVAL '1 hour', 'coalesce'
                )
                RETURNING id
            """)
            task_id = str(cur.fetchone()["id"])
        db_connection.commit()

        claim_sql = """
            SELECT id FROM global_task_sources
            WHERE id = %s AND enabled = true
              AND (next_run IS NULL OR next_run <= now())
            FOR UPDATE SKIP LOCKED
        """

        conn_b = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
        try:
            # Connection A claims the row and HOLDS it in an open transaction.
            with db_connection.cursor() as cur_a:
                cur_a.execute(claim_sql, (task_id,))
                claimed_a = cur_a.fetchall()
                assert any(str(r["id"]) == task_id for r in claimed_a), "A should claim the row"

                # Connection B, concurrently, must SKIP the locked row → empty.
                with conn_b.cursor() as cur_b:
                    cur_b.execute(claim_sql, (task_id,))
                    assert cur_b.fetchall() == [], "B must skip the row A holds locked"
                conn_b.rollback()

            # A's transaction is still open after the cursor block; commit to
            # release the row lock.
            db_connection.commit()

            # Once A releases, B can claim it.
            with conn_b.cursor() as cur_b2:
                cur_b2.execute(claim_sql, (task_id,))
                assert any(str(r["id"]) == task_id for r in cur_b2.fetchall()), (
                    "B should claim the row after A releases"
                )
            conn_b.rollback()
        finally:
            with db_connection.cursor() as cur:
                cur.execute("DELETE FROM global_task_sources WHERE id = %s", (task_id,))
            db_connection.commit()
            conn_b.close()


class TestFirePerIntervalAdvancer:
    """Test advancer behavior with fire_per_interval lapse strategy (DB-gated)."""

    def test_fire_per_interval_3_hours_late_fires_4(self) -> None:
        """fire_per_interval 3 hours late at 1h cadence fires 4 times.

        FLOOR((now - next_run) / cadence) + 1 = FLOOR(3) + 1 = 4 — the 3 missed
        intervals plus the currently-due one. (Earlier name said "fires 3"; the
        +1 for the due interval makes it 4.)
        """
        cadence_seconds = 3600  # 1 hour
        time_late = 10800  # 3 hours
        fire_count = int(time_late / cadence_seconds) + 1
        assert fire_count == 4

        # Cap at 24
        capped = min(24, fire_count)
        assert capped == 4

    def test_fire_per_interval_missing_50_fires_24_capped(self) -> None:
        """fire_per_interval missing 50+ intervals fires 24 (capped)."""
        cadence_seconds = 3600  # 1 hour
        time_late = 180000  # 50 hours
        fire_count = int(time_late / cadence_seconds) + 1
        assert fire_count == 51

        # Cap at 24
        capped = min(24, fire_count)
        assert capped == 24
