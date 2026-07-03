"""Tests for scripts/backfill-outcome-memories.py (#288).

Covers the pure-function helpers and DB-interaction helpers via mock
client.  The full end-to-end dry-run can still be run manually against
the live Supabase project.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from test_utils import FakeClient


# Script has a hyphen in the filename so import it via importlib.
_spec = importlib.util.spec_from_file_location(
    "backfill_outcome_memories",
    Path(__file__).parent.parent / "scripts" / "backfill-outcome-memories.py",
)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


class TestParseIssueNumber:
    def test_github_issue_url(self):
        assert backfill._parse_issue_number("https://github.com/Osasuwu/jarvis/issues/286") == 286

    def test_github_pr_url_returns_none(self):
        """PR URLs must not be treated as issue URLs."""
        assert backfill._parse_issue_number("https://github.com/Osasuwu/jarvis/pull/290") is None

    def test_none_input(self):
        assert backfill._parse_issue_number(None) is None

    def test_empty_string(self):
        assert backfill._parse_issue_number("") is None

    def test_malformed_url(self):
        assert backfill._parse_issue_number("not a url") is None


class TestParsePrNumber:
    def test_github_pr_url(self):
        assert backfill._parse_pr_number("https://github.com/Osasuwu/jarvis/pull/290") == 290

    def test_github_issue_url_returns_none(self):
        assert backfill._parse_pr_number("https://github.com/Osasuwu/jarvis/issues/286") is None

    def test_none_input(self):
        assert backfill._parse_pr_number(None) is None


class TestExtractSingleHash:
    """Only decisions mentioning exactly ONE #N qualify for attribution.
    Multi-issue decisions (sprint planners, batch triages) are too ambiguous."""

    def test_single_hash_returns_int(self):
        assert backfill._extract_single_hash("Implement #286: add memory_id") == 286

    def test_pr_hash_style_also_matches(self):
        """Decision might reference #N as PR number, not just issue."""
        assert (
            backfill._extract_single_hash(
                "Address Copilot review on PR #285 with client-side filter"
            )
            == 285
        )

    def test_zero_hashes_returns_none(self):
        assert backfill._extract_single_hash("Refactor memory server for clarity") is None

    def test_multiple_hashes_returns_none(self):
        """Sprint-opening decisions with 5 issues must NOT attribute to any one."""
        text = (
            "Open Pillar 4 Sprint 'Metacognition Loop-Closure' with 5 issues: "
            "#286, #287, #288, #237, #289."
        )
        assert backfill._extract_single_hash(text) is None

    def test_two_hashes_returns_none(self):
        """Even two #N references are too ambiguous — pick neither."""
        assert backfill._extract_single_hash("Fix #42 then revisit #43") is None

    def test_none_input(self):
        assert backfill._extract_single_hash(None) is None

    def test_empty_string(self):
        assert backfill._extract_single_hash("") is None

    def test_hash_inside_word_still_matches(self):
        """Regex is `#(\\d+)` — will catch `foo#286bar` too. Accept that as
        a known false-positive edge case; real decision text doesn't do this."""
        assert backfill._extract_single_hash("foo#286bar") == 286


# ---------------------------------------------------------------------------
# DB-interaction helpers — mock client
# ---------------------------------------------------------------------------


class TestBuildHashToMemoryIndex:
    def test_populates_from_episodes(self):
        client = FakeClient()
        client.table_handlers["episodes"] = lambda call: [
            {
                "id": "ep-001",
                "kind": "decision_made",
                "payload": {
                    "decision": "Implement #286: add memory_id",
                    "memories_used": ["primary_mem"],
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        idx = backfill._build_hash_to_memory_index(client)
        assert 286 in idx
        assert idx[286] == ("primary_mem", "ep-001", "Implement #286: add memory_id")
        # Verify the query was scoped correctly
        assert ("eq", "kind", "decision_made") in client.table_calls[0]["filters"]
        assert client.table_calls[0]["order"] == ("created_at", False)

    def test_skips_episodes_without_memories_used(self):
        client = FakeClient()
        client.table_handlers["episodes"] = lambda call: [
            {
                "id": "ep-002",
                "kind": "decision_made",
                "payload": {"decision": "Implement #287", "memories_used": []},
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        idx = backfill._build_hash_to_memory_index(client)
        assert 287 not in idx

    def test_skips_episodes_without_hash_in_decision(self):
        client = FakeClient()
        client.table_handlers["episodes"] = lambda call: [
            {
                "id": "ep-003",
                "kind": "decision_made",
                "payload": {
                    "decision": "General refactor",
                    "memories_used": ["some_mem"],
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        idx = backfill._build_hash_to_memory_index(client)
        assert idx == {}

    def test_overwrites_with_newer_decision_same_hash(self):
        client = FakeClient()
        client.table_handlers["episodes"] = lambda call: [
            {
                "id": "ep-old",
                "kind": "decision_made",
                "payload": {
                    "decision": "Fix #100: old version",
                    "memories_used": ["old_mem"],
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "ep-new",
                "kind": "decision_made",
                "payload": {
                    "decision": "Rework #100: new version",
                    "memories_used": ["new_mem"],
                },
                "created_at": "2026-02-01T00:00:00+00:00",
            },
        ]
        idx = backfill._build_hash_to_memory_index(client)
        assert idx[100][0] == "new_mem"  # newer wins

    def test_empty_result_returns_empty_dict(self):
        client = FakeClient()
        client.table_handlers["episodes"] = lambda call: []
        assert backfill._build_hash_to_memory_index(client) == {}


class TestResolveMemoryName:
    def test_found_returns_id(self):
        client = FakeClient()
        client.table_handlers["memories"] = lambda call: [
            {"id": "mem-uuid-abc", "name": "test_mem", "updated_at": "2026-01-01T00:00:00+00:00"},
        ]
        assert backfill._resolve_memory_name(client, "test_mem") == "mem-uuid-abc"
        # Verify the query params: eq name, is_ deleted_at = null, order desc
        assert ("eq", "name", "test_mem") in client.table_calls[0]["filters"]
        assert ("is", "deleted_at", "null") in client.table_calls[0]["filters"]

    def test_not_found_returns_none(self):
        client = FakeClient()
        client.table_handlers["memories"] = lambda call: []
        assert backfill._resolve_memory_name(client, "nonexistent") is None
