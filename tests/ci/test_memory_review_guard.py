"""Tests for memory review schema columns and RPCs (#681).

Validates the SQL migration structure and contract without a database
connection. Actual integration tests (RPC behaviour against a live
Supabase instance) are in tests/test_memory_review_schema.py.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = REPO_ROOT / "supabase" / "migrations"
SCHEMA_PATH = REPO_ROOT / "mcp-memory" / "schema.sql"

# The canonical migration filename for this slice.
EXPECTED_MIGRATION = "20260518000001_add_memory_review_columns.sql"


class TestMigrationFile:
    """The migration file exists and has the expected structure."""

    def test_migration_file_exists(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        assert migration.exists(), (
            f"Expected migration {EXPECTED_MIGRATION} not found in {MIGRATION_DIR}"
        )

    def test_migration_has_requires_review_column(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "requires_review" in text
        assert "ADD COLUMN IF NOT EXISTS requires_review" in text

    def test_migration_has_merge_targets_column(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "merge_targets" in text
        assert "ADD COLUMN IF NOT EXISTS merge_targets" in text

    def test_migration_has_memory_review_decide_rpc(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "CREATE OR REPLACE FUNCTION memory_review_decide" in text

    def test_migration_has_memory_review_list_rpc(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "CREATE OR REPLACE FUNCTION memory_review_list" in text

    def test_migration_accept_action(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "WHEN 'accept'" in text

    def test_migration_accept_with_edit_action(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "WHEN 'accept_with_edit'" in text

    def test_migration_reject_action(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "WHEN 'reject'" in text

    def test_migration_merge_action(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "WHEN 'merge'" in text

    def test_migration_unknown_action_raises(self):
        """The RPC raises on unknown actions per AC."""
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "RAISE EXCEPTION 'Unknown action" in text

    def test_migration_reject_reason_column(self):
        migration = MIGRATION_DIR / EXPECTED_MIGRATION
        text = migration.read_text(encoding="utf-8")
        assert "reject_reason" in text


class TestSchemaFileInSync:
    """schema.sql must reflect the same columns and RPCs as the migration."""

    def test_schema_has_requires_review_column(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS requires_review" in text

    def test_schema_has_merge_targets_column(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS merge_targets" in text

    def test_schema_has_reject_reason_column(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS reject_reason" in text

    def test_schema_has_memory_review_decide(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "CREATE OR REPLACE FUNCTION memory_review_decide" in text

    def test_schema_has_memory_review_list(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "CREATE OR REPLACE FUNCTION memory_review_list" in text

    def test_schema_has_merge_index(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "idx_memories_merge_targets" in text

    def test_schema_has_review_index(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "idx_memories_requires_review" in text
