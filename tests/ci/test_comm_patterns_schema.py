"""Meta-test for `comm_patterns` schema slice (#580, ADR 0004).

Locks down the column set, indices, and `primary_label` CHECK enum so a
later edit that quietly drops a column or widens the enum trips CI rather
than landing silently. Same pattern as `test_schema_drift_guard.py` (#326)
and `test_pr_body_check_guard.py`: a parallel reimplementation of the
load-bearing invariant, kept next to the canonical source it guards.

The taxonomy is re-derivable, but every re-derivation should be a
deliberate ADR update — not a drive-by edit. This test is the gate.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "mcp-memory" / "schema.sql"
ADR_PATH = REPO_ROOT / "docs" / "adr" / "0004-comm-patterns-taxonomy.md"

EXPECTED_LABELS = {
    "correction_wrong_direction",
    "correction_incomplete",
    "affirmation",
    "affirmation_with_redirect",
    "preference_directive",
    "meta_protocol",
}

EXPECTED_COLUMNS = {
    "id",
    "device",
    "session_id",
    "message_idx",
    "captured_at",
    "primary_label",
    "subtype",
    "confidence",
    "anchor_quote",
    "redacted",
    "embedding",
    "source_provenance",
    "created_at",
}

EXPECTED_INDICES = {
    "idx_comm_patterns_label_captured",
    "idx_comm_patterns_dedup",
    "idx_comm_patterns_no_embedding",
}


def _schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _comm_patterns_block() -> str:
    """Return the `create table comm_patterns (...)` body, parens-balanced."""
    text = _schema()
    m = re.search(r"create table if not exists comm_patterns\s*\(", text)
    assert m, "comm_patterns table not declared in schema.sql"
    start = m.end() - 1  # position of opening paren
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[m.start() : i + 1]
    raise AssertionError("Unterminated `create table comm_patterns` block")


class TestTableShape:
    def test_table_declared(self):
        assert "create table if not exists comm_patterns" in _schema(), (
            f"Expected `create table if not exists comm_patterns` in {SCHEMA_PATH.relative_to(REPO_ROOT)}"
        )

    @pytest.mark.parametrize("col", sorted(EXPECTED_COLUMNS))
    def test_column_present(self, col):
        body = _comm_patterns_block()
        # Match column at line start (allowing leading whitespace), followed by
        # whitespace and a type token.
        pat = rf"(?m)^\s*{re.escape(col)}\s+\w"
        assert re.search(pat, body), (
            f"Column `{col}` missing from comm_patterns. "
            f"If you intentionally dropped it, update ADR 0004 first."
        )

    def test_no_project_column(self):
        """ADR 0004 §3 — comm_patterns is global; no project pinning."""
        body = _comm_patterns_block()
        assert not re.search(r"(?m)^\s*project\s+\w", body), (
            "comm_patterns must NOT have a `project` column (ADR 0004 §3)."
        )

    def test_anchor_quote_is_single_column(self):
        """ADR 0004 §2 — store one scrubbed anchor, not raw + redacted side by side."""
        body = _comm_patterns_block()
        assert re.search(r"(?m)^\s*anchor_quote\s+text\s+not\s+null", body)
        assert not re.search(r"(?m)^\s*anchor_quote_raw\s+", body), (
            "Storing `anchor_quote_raw` defeats the redaction boundary (ADR 0004 §2)."
        )


class TestEnum:
    def test_primary_label_check_constraint_lists_six_labels(self):
        body = _comm_patterns_block()
        m = re.search(
            r"primary_label\s+text\s+not\s+null\s+check\s*\(\s*primary_label\s+in\s*\((.*?)\)\s*\)",
            body,
            re.S,
        )
        assert m, (
            "primary_label must have a CHECK (primary_label IN (...)) constraint."
        )
        listed = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        assert listed == EXPECTED_LABELS, (
            f"primary_label enum drift.\n"
            f"  expected: {sorted(EXPECTED_LABELS)}\n"
            f"  found:    {sorted(listed)}\n"
            f"If intentional, update ADR 0004 §1 + this test in the same PR."
        )


class TestIndices:
    @pytest.mark.parametrize("idx", sorted(EXPECTED_INDICES))
    def test_index_declared(self, idx):
        assert idx in _schema(), (
            f"Index `{idx}` missing from schema.sql (ADR 0004)."
        )

    def test_dedup_is_unique(self):
        text = _schema()
        m = re.search(
            r"create unique index if not exists idx_comm_patterns_dedup\s+on\s+comm_patterns\s*\(([^)]+)\)",
            text,
        )
        assert m, "idx_comm_patterns_dedup must be a UNIQUE index."
        cols = [c.strip() for c in m.group(1).split(",")]
        assert cols == ["device", "session_id", "message_idx"], (
            f"Dedup index columns drift: {cols}. Stop-hook idempotency relies on this exact tuple."
        )


class TestWatermark:
    def test_watermark_table_declared(self):
        assert "create table if not exists comm_patterns_watermark" in _schema()

    def test_watermark_pk_is_device_session(self):
        text = _schema()
        m = re.search(
            r"create table if not exists comm_patterns_watermark\s*\((.*?)\);",
            text,
            re.S,
        )
        assert m, "comm_patterns_watermark not declared"
        body = m.group(1)
        assert re.search(r"primary key\s*\(\s*device\s*,\s*session_id\s*\)", body), (
            "Watermark PK must be (device, session_id) — Stop-hook idempotency relies on this."
        )


class TestRLS:
    @pytest.mark.parametrize("table", ["comm_patterns", "comm_patterns_watermark"])
    def test_rls_enabled(self, table):
        assert f"alter table {table} enable row level security" in _schema(), (
            f"RLS must be enabled on `{table}` (consistency with events_canonical et al.)."
        )


class TestADR:
    def test_adr_exists(self):
        assert ADR_PATH.exists(), (
            f"ADR 0004 missing at {ADR_PATH.relative_to(REPO_ROOT)}"
        )

    def test_adr_lists_all_labels(self):
        text = ADR_PATH.read_text(encoding="utf-8")
        for label in EXPECTED_LABELS:
            assert label in text, (
                f"ADR 0004 must document `{label}` (re-derivation rationale lives there)."
            )
