"""Meta-test for sandcastle anon-INSERT RLS gate (slice 3, #542).

Same pattern as test_schema_drift_guard.py (#326): parse the migration SQL,
reimplement the policy decision rule in Python, assert positive + negative
cases. CI does not require a live DB — parsing alone catches drift between
schema.sql and the migration file, plus any regression in policy shape.

Two layers:

1. **Migration shape** — the migration must (a) drop the legacy broad
   "Allow all for anon" policy on each of the four tables, and (b) install
   a "FOR INSERT TO anon" policy whose WITH CHECK clause references the
   per-table provenance column with a 'sandcastle:%' LIKE prefix.

2. **Policy logic** — given a candidate row payload, a pure-Python
   reimplementation of the WITH CHECK predicate must accept rows whose
   per-table provenance column starts with 'sandcastle:' and reject all
   others. Service-role writes bypass RLS, modeled here as `role='service'`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260508120000_sandcastle_anon_rls_provenance_gate.sql"
)
UPDATE_DELETE_MIGRATION_PATH = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260508130000_sandcastle_anon_rls_update_delete_gate.sql"
)
SCHEMA_PATH = REPO_ROOT / "mcp-memory" / "schema.sql"

# Per-table provenance column. memories + task_outcomes use source_provenance;
# episodes + events_canonical use the existing `actor` column (semantic match —
# `actor` is already the provenance field per their schema comments).
PROVENANCE_COLUMN = {
    "memories": "source_provenance",
    "task_outcomes": "source_provenance",
    "episodes": "actor",
    "events_canonical": "actor",
}


# -- Migration shape --------------------------------------------------------


def _migration_sql() -> str:
    assert MIGRATION_PATH.exists(), f"Missing migration: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _update_delete_migration_sql() -> str:
    assert UPDATE_DELETE_MIGRATION_PATH.exists(), (
        f"Missing migration: {UPDATE_DELETE_MIGRATION_PATH}"
    )
    return UPDATE_DELETE_MIGRATION_PATH.read_text(encoding="utf-8")


def _schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class TestMigrationShape:
    def test_migration_file_exists(self):
        assert MIGRATION_PATH.exists()

    def test_task_outcomes_gets_source_provenance_column(self):
        sql = _migration_sql()
        assert re.search(
            r"ALTER\s+TABLE\s+task_outcomes\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+source_provenance",
            sql,
            re.IGNORECASE,
        ), "Migration must add source_provenance column to task_outcomes"

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_drops_legacy_anon_policy(self, table: str):
        sql = _migration_sql()
        pattern = rf'DROP\s+POLICY\s+IF\s+EXISTS\s+"Allow all for anon"\s+ON\s+{table}\b'
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Migration must drop legacy 'Allow all for anon' policy on {table}"
        )

    @pytest.mark.parametrize("table,column", list(PROVENANCE_COLUMN.items()))
    def test_installs_sandcastle_insert_policy(self, table: str, column: str):
        sql = _migration_sql()
        # Match: CREATE POLICY ... ON <table> FOR INSERT TO anon WITH CHECK (<col> LIKE 'sandcastle:%')
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+INSERT\s+TO\s+anon\s+"
            rf"WITH\s+CHECK\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Missing or malformed sandcastle INSERT policy on {table} "
            f"(expected WITH CHECK ({column} LIKE 'sandcastle:%'))"
        )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_anon_select_preserved(self, table: str):
        """SELECT for anon must remain wide-open — only INSERT is gated."""
        sql = _migration_sql()
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+SELECT\s+TO\s+anon\s+USING\s*\(\s*true\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Anon SELECT policy missing or restrictive on {table}"
        )


class TestSchemaMirror:
    """schema.sql is the canonical doc; migration is the apply unit. Both
    must agree on the new policy shape (per #326 schema-drift CI gate)."""

    @pytest.mark.parametrize("table,column", list(PROVENANCE_COLUMN.items()))
    def test_schema_has_sandcastle_insert_policy(self, table: str, column: str):
        sql = _schema_sql()
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+INSERT\s+TO\s+anon\s+"
            rf"WITH\s+CHECK\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"schema.sql is missing the sandcastle INSERT policy for {table}; "
            f"it has drifted from the migration."
        )

    def test_schema_has_no_legacy_broad_anon_policy(self):
        """The phrase 'Allow all for anon' is the legacy broad policy we
        replaced. If it survives in schema.sql for any of the four tables,
        the schema has drifted from the migration."""
        sql = _schema_sql()
        for table in PROVENANCE_COLUMN:
            pattern = rf'"Allow all for anon"\s+(?:on|ON)\s+{table}\b'
            assert not re.search(pattern, sql), (
                f"Legacy 'Allow all for anon' policy still present on {table} "
                f"in schema.sql — drift from migration."
            )


# -- Policy logic -----------------------------------------------------------


def _anon_insert_allowed(table: str, row: dict) -> bool:
    """Pure-Python reimplementation of the anon INSERT WITH CHECK predicate.

    Mirrors the migration's per-table policy. Keep in sync with
    `20260508120000_sandcastle_anon_rls_provenance_gate.sql`. The shape tests
    above lock down the SQL; this function locks down the decision logic.
    """
    column = PROVENANCE_COLUMN[table]
    value = row.get(column)
    if value is None:
        return False
    return value.startswith("sandcastle:")


class TestPolicyLogic:
    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_sandcastle_prefix_accepted(self, table: str):
        col = PROVENANCE_COLUMN[table]
        assert _anon_insert_allowed(table, {col: "sandcastle:agent"})
        assert _anon_insert_allowed(table, {col: "sandcastle:agent:run-42"})

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_other_prefix_rejected(self, table: str):
        col = PROVENANCE_COLUMN[table]
        for bad in [
            "session:abc",
            "skill:implement",
            "user:explicit",
            "Sandcastle:agent",  # case-sensitive — LIKE in PG is case-sensitive
            "sandcastles:agent",  # extra 's' — prefix is 'sandcastles:', not 'sandcastle:'
            "",
            "sandcastle",  # no colon
        ]:
            assert not _anon_insert_allowed(table, {col: bad}), (
                f"{table}: value {bad!r} should have been rejected"
            )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_null_provenance_rejected(self, table: str):
        col = PROVENANCE_COLUMN[table]
        assert not _anon_insert_allowed(table, {col: None})
        assert not _anon_insert_allowed(table, {})  # missing key

    def test_service_role_bypasses_rls(self):
        """Service-role bypasses RLS at the Postgres level — modeled here as
        a documentation assertion rather than a code path. The migration does
        not touch service-role behavior; the test exists to flag any future
        change that would (e.g. adding TO service_role to the new policies)."""
        sql = _migration_sql()
        # No new policy should mention service_role explicitly — service-role
        # bypasses RLS and should not need a policy.
        assert "service_role" not in sql.lower(), (
            "Migration should not reference service_role — it bypasses RLS. "
            "If you intentionally added a service_role policy, update this test."
        )


# -- Slice 3.5 (#565): UPDATE + DELETE gated on provenance --------------------


class TestUpdateDeleteMigrationShape:
    """Slice 3.5 migration drops the open anon UPDATE/DELETE policies and
    replaces them with provenance-gated ones (USING + WITH CHECK on the
    per-table provenance column)."""

    def test_migration_file_exists(self):
        assert UPDATE_DELETE_MIGRATION_PATH.exists()

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_drops_open_update_policy(self, table: str):
        sql = _update_delete_migration_sql()
        pattern = rf'DROP\s+POLICY\s+IF\s+EXISTS\s+"Anon update"\s+ON\s+{table}\b'
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Migration must drop legacy open 'Anon update' policy on {table}"
        )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_drops_open_delete_policy(self, table: str):
        sql = _update_delete_migration_sql()
        pattern = rf'DROP\s+POLICY\s+IF\s+EXISTS\s+"Anon delete"\s+ON\s+{table}\b'
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Migration must drop legacy open 'Anon delete' policy on {table}"
        )

    @pytest.mark.parametrize("table,column", list(PROVENANCE_COLUMN.items()))
    def test_installs_sandcastle_update_policy(self, table: str, column: str):
        sql = _update_delete_migration_sql()
        # CREATE POLICY ... ON <t> FOR UPDATE TO anon USING (<col> LIKE 'sandcastle:%') WITH CHECK (<col> LIKE 'sandcastle:%')
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+UPDATE\s+TO\s+anon\s+"
            rf"USING\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)\s+"
            rf"WITH\s+CHECK\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Missing or malformed sandcastle UPDATE policy on {table} "
            f"(expected USING + WITH CHECK ({column} LIKE 'sandcastle:%'))"
        )

    @pytest.mark.parametrize("table,column", list(PROVENANCE_COLUMN.items()))
    def test_installs_sandcastle_delete_policy(self, table: str, column: str):
        sql = _update_delete_migration_sql()
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+DELETE\s+TO\s+anon\s+"
            rf"USING\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"Missing or malformed sandcastle DELETE policy on {table} "
            f"(expected USING ({column} LIKE 'sandcastle:%'))"
        )

    def test_no_service_role_reference(self):
        sql = _update_delete_migration_sql()
        assert "service_role" not in sql.lower(), (
            "Slice 3.5 migration should not reference service_role — it bypasses RLS."
        )


class TestUpdateDeleteSchemaMirror:
    """schema.sql must mirror the slice-3.5 UPDATE/DELETE policies and must
    not retain the legacy open 'Anon update' / 'Anon delete' policies on the
    four sandcastle-touched tables (#326 schema-drift gate)."""

    @pytest.mark.parametrize("table,column", list(PROVENANCE_COLUMN.items()))
    def test_schema_has_sandcastle_update_policy(self, table: str, column: str):
        sql = _schema_sql()
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+UPDATE\s+TO\s+anon\s+"
            rf"USING\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)\s+"
            rf"WITH\s+CHECK\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"schema.sql missing sandcastle UPDATE policy for {table} — drift from migration"
        )

    @pytest.mark.parametrize("table,column", list(PROVENANCE_COLUMN.items()))
    def test_schema_has_sandcastle_delete_policy(self, table: str, column: str):
        sql = _schema_sql()
        pattern = (
            rf"CREATE\s+POLICY\s+\"[^\"]+\"\s+ON\s+{table}\s+"
            rf"FOR\s+DELETE\s+TO\s+anon\s+"
            rf"USING\s*\(\s*{column}\s+LIKE\s+'sandcastle:%'\s*\)"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            f"schema.sql missing sandcastle DELETE policy for {table} — drift from migration"
        )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_schema_has_no_open_update_policy(self, table: str):
        """An unconditional 'Anon update' (USING (true) WITH CHECK (true)) on
        any of the four tables means slice 3.5 was not mirrored into
        schema.sql."""
        sql = _schema_sql()
        # Match the legacy shape specifically: USING (true) WITH CHECK (true)
        pattern = (
            rf'"Anon update"\s+ON\s+{table}\s+'
            rf"FOR\s+UPDATE\s+TO\s+anon\s+USING\s*\(\s*true\s*\)"
        )
        assert not re.search(pattern, sql, re.IGNORECASE), (
            f"Legacy open 'Anon update' policy still in schema.sql for {table}"
        )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_schema_has_no_open_delete_policy(self, table: str):
        sql = _schema_sql()
        pattern = (
            rf'"Anon delete"\s+ON\s+{table}\s+'
            rf"FOR\s+DELETE\s+TO\s+anon\s+USING\s*\(\s*true\s*\)"
        )
        assert not re.search(pattern, sql, re.IGNORECASE), (
            f"Legacy open 'Anon delete' policy still in schema.sql for {table}"
        )


# -- Slice 3.5 policy logic --------------------------------------------------


def _anon_update_allowed(table: str, existing_row: dict, new_row: dict) -> bool:
    """Pure-Python reimplementation of the anon UPDATE policy.

    Both USING (existing row) and WITH CHECK (new row) must satisfy the
    sandcastle prefix predicate. This blocks two classes of forgery:
      - touching a non-sandcastle row at all (USING fails)
      - rewriting provenance from sandcastle → non-sandcastle (WITH CHECK fails)
    """
    column = PROVENANCE_COLUMN[table]
    existing = existing_row.get(column)
    new = new_row.get(column, existing)  # unchanged if not in update payload
    if existing is None or new is None:
        return False
    return existing.startswith("sandcastle:") and new.startswith("sandcastle:")


def _anon_delete_allowed(table: str, row: dict) -> bool:
    """Pure-Python reimplementation of the anon DELETE policy."""
    column = PROVENANCE_COLUMN[table]
    value = row.get(column)
    if value is None:
        return False
    return value.startswith("sandcastle:")


class TestUpdateDeletePolicyLogic:
    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_update_sandcastle_to_sandcastle_accepted(self, table: str):
        col = PROVENANCE_COLUMN[table]
        assert _anon_update_allowed(
            table,
            existing_row={col: "sandcastle:agent:run-1", "data": "old"},
            new_row={col: "sandcastle:agent:run-1", "data": "new"},
        )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_update_non_sandcastle_row_rejected(self, table: str):
        """Touching a host-owned row (provenance not sandcastle) must fail USING."""
        col = PROVENANCE_COLUMN[table]
        for hostlike in ["session:abc", "skill:implement", "user:explicit", "hook:foo"]:
            assert not _anon_update_allowed(
                table,
                existing_row={col: hostlike},
                new_row={col: hostlike, "data": "rewrite"},
            ), f"{table}: anon UPDATE should reject host-owned row {hostlike!r}"

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_update_provenance_forge_rejected(self, table: str):
        """Anon must not be able to rewrite provenance away from sandcastle:
        (audit erase) or rewrite into sandcastle: from non-sandcastle (forge)."""
        col = PROVENANCE_COLUMN[table]
        # Audit-erase: sandcastle row → host-owned provenance (WITH CHECK fails)
        assert not _anon_update_allowed(
            table,
            existing_row={col: "sandcastle:agent"},
            new_row={col: "user:explicit"},
        ), f"{table}: anon UPDATE should reject sandcastle → user:explicit (audit erase)"
        # Forge: host row → sandcastle (USING fails — covered by test_update_non_sandcastle_row_rejected,
        # but include the symmetric direction here for clarity)
        assert not _anon_update_allowed(
            table,
            existing_row={col: "skill:implement"},
            new_row={col: "sandcastle:agent"},
        ), f"{table}: anon UPDATE should reject skill:* → sandcastle (forge)"

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_delete_sandcastle_accepted(self, table: str):
        col = PROVENANCE_COLUMN[table]
        assert _anon_delete_allowed(table, {col: "sandcastle:agent:run-7"})

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_delete_non_sandcastle_rejected(self, table: str):
        col = PROVENANCE_COLUMN[table]
        for bad in [
            "session:abc",
            "skill:implement",
            "user:explicit",
            "hook:user-prompt-submit",
            "Sandcastle:agent",  # case-sensitive
            "sandcastles:agent",  # extra 's'
            "",
            None,
        ]:
            assert not _anon_delete_allowed(table, {col: bad}), (
                f"{table}: anon DELETE should reject row with provenance {bad!r}"
            )

    @pytest.mark.parametrize("table", list(PROVENANCE_COLUMN.keys()))
    def test_delete_null_provenance_rejected(self, table: str):
        col = PROVENANCE_COLUMN[table]
        assert not _anon_delete_allowed(table, {col: None})
        assert not _anon_delete_allowed(table, {})
