"""Meta-test for memory-deriver schema migration (Slice 1, #552).

Locks down the column additions on the `memories` table so that a later
edit that drops a column, changes a type, or alters a default trips CI
rather than silently diverging from the docs.

Columns guarded:
  - requires_review BOOL NOT NULL DEFAULT FALSE
  - derivation_run_id UUID NULL
  - merge_targets UUID[] NULL

Existing columns relied on by the deriver subsystem (verified by the
pre-migration collision precheck, not re-verified here):
  - confidence (REAL / NUMERIC)
  - superseded_by (UUID)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "mcp-memory" / "schema.sql"

# Columns the migration adds to the memories table (issue #552, Slice 1).
NEW_COLUMNS: dict[str, dict] = {
    "requires_review": {
        "type": "bool",
        "nullable": False,
        "default": "false",
    },
    "derivation_run_id": {
        "type": "uuid",
        "nullable": True,
        "default": None,
    },
    "merge_targets": {
        "type": "uuid[]",
        "nullable": True,
        "default": None,
    },
}

# Existing columns the deriver subsystem depends on (verified by precheck,
# documented here for the record).
EXISTING_DERIVER_COLUMNS: dict[str, dict] = {
    "confidence": {"type": "real", "info": "line 592 — REAL DEFAULT 0.5"},
    "superseded_by": {"type": "uuid", "info": "line 585 — UUID NULL, FK memories(id)"},
    "source_provenance": {"type": "text", "info": "line 599 — TEXT NULL"},
}


def _schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _deriver_block() -> str | None:
    """Return the deriver migration block (header + DDL, up to next section)."""
    text = _schema()
    m = re.search(
        r"Implicit memory derivation: Deriver/Dreamer columns \(issue #552",
        text,
    )
    if not m:
        return None
    start = m.start()
    remaining = text[start:]
    # Skip past the opening comment block to find the first DDL statement,
    # then search for the next section header after that.
    ddl_start = remaining.find("alter table")
    if ddl_start == -1:
        return None
    # Search for the next section header (-- ====) starting after the DDL begins
    next_sec = re.search(r"\n-- ={2,}", remaining[ddl_start:])
    if next_sec:
        return remaining[: ddl_start + next_sec.start()]
    return remaining


class TestDeriverColumns:
    def test_deriver_block_present(self):
        """The deriver migration block must exist in schema.sql."""
        assert _deriver_block() is not None, (
            f"No deriver migration block found in {SCHEMA_PATH.relative_to(REPO_ROOT)}. "
            "Expected comment: 'Implicit memory derivation: Deriver/Dreamer columns (issue #552)'"
        )

    @pytest.mark.parametrize("col", sorted(NEW_COLUMNS))
    def test_new_column_added(self, col):
        """Each new column must appear in an ALTER TABLE ADD COLUMN."""
        body = _deriver_block()
        assert body is not None
        pat = rf"alter table memories add column if not exists {re.escape(col)}\s+"
        assert re.search(pat, body, re.I), (
            f"Column `{col}` missing from deriver migration. "
            "Expected `alter table memories add column if not exists {col}`."
        )

    def test_requires_review_default_false(self):
        """requires_review must have NOT NULL DEFAULT FALSE."""
        body = _deriver_block()
        assert body is not None
        assert re.search(
            r"requires_review\s+bool(?:ean)?\s+not\s+null\s+default\s+false",
            body,
            re.I,
        ), "requires_review must be `boolean NOT NULL DEFAULT false` (bool alias accepted)."

    def test_derivation_run_id_nullable(self):
        """derivation_run_id must be UUID and nullable (NULL = pre-derivation)."""
        body = _deriver_block()
        assert body is not None
        # Can be either "uuid" alone or "uuid" without NOT NULL
        assert re.search(
            r"derivation_run_id\s+uuid",
            body,
            re.I,
        ), "derivation_run_id must be `uuid`."
        assert not re.search(
            r"derivation_run_id\s+uuid\s+not\s+null",
            body,
            re.I,
        ), "derivation_run_id must be nullable (no NOT NULL)."

    def test_merge_targets_array(self):
        """merge_targets must be UUID[], nullable."""
        body = _deriver_block()
        assert body is not None
        assert re.search(
            r"merge_targets\s+uuid\[\]",
            body,
            re.I,
        ), "merge_targets must be `uuid[]`."
        assert not re.search(
            r"merge_targets\s+uuid\[\]\s+not\s+null",
            body,
            re.I,
        ), "merge_targets must be nullable."

    def test_no_no_op_backfill_updates(self):
        """The migration must NOT contain backfill UPDATEs.

        `ADD COLUMN ... NOT NULL DEFAULT false` on PG 11+ is a catalog-only
        operation; existing rows pick up the default at zero I/O cost. A
        follow-up `UPDATE memories SET requires_review = false WHERE ...`
        is a full seqscan + ROW EXCLUSIVE lock for zero effect on the live
        shared table. Same logic for `derivation_run_id` (added nullable,
        no default — all existing rows are already NULL).
        """
        body = _deriver_block()
        assert body is not None
        assert not re.search(
            r"^\s*update\s+memories\s+set\s+requires_review",
            body,
            re.I | re.M,
        ), "Drop the requires_review backfill UPDATE — DEFAULT covers existing rows."
        assert not re.search(
            r"^\s*update\s+memories\s+set\s+derivation_run_id",
            body,
            re.I | re.M,
        ), "Drop the derivation_run_id backfill UPDATE — column is already NULL."

    def test_index_derivation_run_id(self):
        """Partial index on derivation_run_id for Dreamer skip-logic lookups."""
        body = _deriver_block()
        assert body is not None
        assert "idx_memories_derivation_run_id" in body, (
            "Missing partial index `idx_memories_derivation_run_id` for "
            "Dreamer's `derivation_run_id = $1` equality lookups."
        )

    def test_merge_targets_check_constraint(self):
        """CHECK forbids empty arrays so NULL is the only `not a proposal` state.

        Required to keep the GIN partial predicate
        `WHERE merge_targets IS NOT NULL` aligned with the recall filter
        `merge_targets IS NULL`. Without the constraint, an empty array
        (`'{}'`) is non-null, enters the index, and is treated by recall
        as `not a proposal` — boundary inconsistency between the index
        and the query.
        """
        body = _deriver_block()
        assert body is not None
        assert re.search(
            r"check\s*\(\s*merge_targets\s+is\s+null\s+or\s+array_length\(\s*merge_targets",
            body,
            re.I,
        ), (
            "Missing CHECK constraint forbidding empty merge_targets arrays. "
            "Expected: `check (merge_targets is null or array_length(merge_targets, 1) > 0)`."
        )

    def test_index_requires_review(self):
        """Index on requires_review for SessionStart scan."""
        body = _deriver_block()
        assert body is not None
        assert "idx_memories_requires_review" in body, (
            "Missing index `idx_memories_requires_review` for review-scan."
        )

    def test_index_merge_targets(self):
        """GIN index on merge_targets for recall filter."""
        body = _deriver_block()
        assert body is not None
        assert "idx_memories_merge_targets" in body, (
            "Missing index `idx_memories_merge_targets` for recall filter."
        )


class TestDeriverDocumentation:
    def test_provenance_namespaces_documented(self):
        """The migration block must document the new provenance namespaces.

        Requires at least one of `hook:deriver` or `task:dreamer` to appear
        verbatim in the block. The previous `or "deriver" in body.lower()`
        fallback was tautological — every `add column ... derivation_run_id`
        line satisfied it, so the test could never fail.
        """
        body = _deriver_block()
        assert body is not None
        has_hook_deriver = "hook:deriver" in body
        has_task_dreamer = "task:dreamer" in body
        assert has_hook_deriver or has_task_dreamer, (
            "Deriver migration block must document provenance namespace conventions. "
            "Expected verbatim `hook:deriver` or `task:dreamer` in the block."
        )

    def test_links_to_decisions(self):
        """The migration must reference the ADR decisions it implements."""
        body = _deriver_block()
        assert body is not None
        assert "31ebba19" in body and "d162cca4" in body, (
            "Expected decision ID references: 31ebba19 (always-gate) and d162cca4 "
            "(merge_proposal_shape). These link the DDL to ADR-0003."
        )


class TestExistingDeriverColumns:
    """Assert the existing columns the deriver subsystem depends on really
    exist in `mcp-memory/schema.sql`. Without these, the precheck claim
    `verified by precheck` is unverified by anything in this repo.
    """

    @pytest.mark.parametrize("col", sorted(EXISTING_DERIVER_COLUMNS))
    def test_existing_column_present(self, col):
        text = _schema()
        # Match `add column [if not exists] <col> ` or `<col>  <type>` in
        # the canonical create-table statement.
        pat = rf"\b{re.escape(col)}\s+(uuid|real|numeric|text|boolean|bool|float)\b"
        assert re.search(pat, text, re.I), (
            f"Column `{col}` (relied on by deriver subsystem) not found in "
            f"{SCHEMA_PATH.relative_to(REPO_ROOT)}. EXISTING_DERIVER_COLUMNS docstring "
            f"says `verified by precheck` — this test verifies the column actually exists."
        )


class TestPrecheckScript:
    def test_precheck_script_exists(self):
        assert (REPO_ROOT / "scripts" / "check-memory-deriver-schema.py").exists(), (
            "Pre-migration collision precheck script missing at scripts/check-memory-deriver-schema.py"
        )

    def test_precheck_aborts_on_incompatible_superseded_by(self):
        text = (REPO_ROOT / "scripts" / "check-memory-deriver-schema.py").read_text()
        assert "superseded_by" in text
        assert "sys.exit(1)" in text, (
            "Precheck must abort (sys.exit(1)) when superseded_by has incompatible type."
        )

    def test_precheck_documents_confidence_compatibility(self):
        text = (REPO_ROOT / "scripts" / "check-memory-deriver-schema.py").read_text()
        assert "confidence" in text
        assert "numeric_types" in text, (
            "Precheck must verify confidence is a numeric type (real, numeric, float4, float8)."
        )
