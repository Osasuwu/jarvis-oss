"""Tests for scripts/backfill-outcome-memories.py (#288).

Covers the pure-function helpers and DB-interaction helpers via mock
client.  The full end-to-end dry-run can still be run manually against
the live Supabase project.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from supabase_stubs import FakeClient


# Script has a hyphen in the filename so import it via importlib.
_spec = importlib.util.spec_from_file_location(
    "backfill_outcome_memories",
    Path(__file__).parent.parent.parent / "scripts" / "backfill-outcome-memories.py",
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


class TestIsUuidShaped:
    """AC 1: UUID-shaped entries should be distinguished from name strings."""

    def test_valid_uuid_v4_returns_true(self):
        """Standard UUID v4 format."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert backfill._is_uuid_shaped(uuid) is True

    def test_valid_uuid_no_hyphens_returns_true(self):
        """UUID without hyphens should also match."""
        uuid = "550e8400e29b41d4a716446655440000"
        assert backfill._is_uuid_shaped(uuid) is True

    def test_short_string_returns_false(self):
        """Memory names like 'primary_mem' are not UUIDs."""
        assert backfill._is_uuid_shaped("primary_mem") is False

    def test_empty_string_returns_false(self):
        assert backfill._is_uuid_shaped("") is False

    def test_none_returns_false(self):
        assert backfill._is_uuid_shaped(None) is False

    def test_numeric_string_returns_false(self):
        assert backfill._is_uuid_shaped("12345") is False


class TestResolveMemoryUuid:
    """AC 1: Direct UUID existence check against memories(id)."""

    def test_existing_uuid_returns_id(self):
        """UUID that exists in memories table."""
        client = FakeClient()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        client.table_handlers["memories"] = lambda call: [
            {"id": uuid, "name": "some_name", "updated_at": "2026-01-01T00:00:00+00:00"},
        ]
        assert backfill._resolve_memory_uuid(client, uuid) == uuid
        # Verify it queries by id, not by name
        assert ("eq", "id", uuid) in client.table_calls[0]["filters"]

    def test_dangling_uuid_returns_none(self):
        """UUID that doesn't exist in memories table (dangling reference)."""
        client = FakeClient()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        client.table_handlers["memories"] = lambda call: []
        assert backfill._resolve_memory_uuid(client, uuid) is None

    def test_queries_with_deleted_at_check(self):
        """Should only match non-deleted memories."""
        client = FakeClient()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        client.table_handlers["memories"] = lambda call: []
        backfill._resolve_memory_uuid(client, uuid)
        # Verify it filters by is_("deleted_at", "null")
        assert ("is", "deleted_at", "null") in client.table_calls[0]["filters"]


class TestBuildHashToMemoryIndexUuid:
    """AC 1 & 2: Handle UUID entries in memories_used[0]."""

    def test_uuid_shaped_entry_preserved_in_index(self):
        """UUID-shaped entries should be stored as-is, not treated as names."""
        client = FakeClient()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        client.table_handlers["episodes"] = lambda call: [
            {
                "id": "ep-001",
                "kind": "decision_made",
                "payload": {
                    "decision": "Implement #286: add memory_id",
                    "memories_used": [uuid],  # UUID, not name
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        idx = backfill._build_hash_to_memory_index(client)
        assert 286 in idx
        # The stored value should be the UUID as-is
        assert idx[286][0] == uuid

    def test_name_string_still_stored(self):
        """Non-UUID strings should still be stored (for backward compat)."""
        client = FakeClient()
        client.table_handlers["episodes"] = lambda call: [
            {
                "id": "ep-001",
                "kind": "decision_made",
                "payload": {
                    "decision": "Implement #286: add memory_id",
                    "memories_used": ["primary_mem"],  # Name string
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        idx = backfill._build_hash_to_memory_index(client)
        assert idx[286][0] == "primary_mem"


class TestResolveMemoryRef:
    """AC 2: UUID-first resolution strategy (try UUID, then name)."""

    def test_uuid_shaped_and_exists_uses_directly(self):
        """AC 2: UUID entry that exists should be used directly."""
        client = FakeClient()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        client.table_handlers["memories"] = lambda call: [
            {"id": uuid, "name": "some_name", "updated_at": "2026-01-01T00:00:00+00:00"},
        ]
        result, is_dangling = backfill._resolve_memory_ref(client, uuid)
        assert result == uuid
        assert is_dangling is False

    def test_uuid_shaped_but_dangling_reported_as_dangling(self):
        """AC 3: UUID entry that doesn't exist is tracked as dangling."""
        client = FakeClient()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        client.table_handlers["memories"] = lambda call: []
        result, is_dangling = backfill._resolve_memory_ref(client, uuid)
        assert result is None
        assert is_dangling is True

    def test_name_string_falls_back_to_name_resolution(self):
        """AC 2: Non-UUID entries use name resolution."""
        client = FakeClient()
        client.table_handlers["memories"] = lambda call: [
            {"id": "mem-abc123", "name": "primary_mem", "updated_at": "2026-01-01T00:00:00+00:00"},
        ]
        result, is_dangling = backfill._resolve_memory_ref(client, "primary_mem")
        assert result == "mem-abc123"
        assert is_dangling is False

    def test_name_string_not_found_not_dangling(self):
        """AC 2: Unresolved names are not marked as dangling (they could be renamed)."""
        client = FakeClient()
        client.table_handlers["memories"] = lambda call: []
        result, is_dangling = backfill._resolve_memory_ref(client, "nonexistent_name")
        assert result is None
        assert is_dangling is False
