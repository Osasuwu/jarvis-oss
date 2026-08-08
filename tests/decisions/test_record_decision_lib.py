"""Unit tests for record_decision helper functions.

Covers:
  - _looks_like_uuid — UUID string detection
  - _resolve_memory_refs — memory name→UUID resolution
"""

from __future__ import annotations

from server import _looks_like_uuid, _resolve_memory_refs

from record_decision_doubles import UID_A, UID_B, UID_C, resolver_client


class TestLooksLikeUuid:
    def test_accepts_canonical(self):
        assert _looks_like_uuid(UID_A) is True

    def test_accepts_uppercase(self):
        # uuid.UUID accepts case-insensitively — important because callers
        # sometimes paste UUIDs copied from PostgREST/Supabase logs.
        assert _looks_like_uuid(UID_A.upper()) is True

    def test_rejects_plain_name(self):
        assert _looks_like_uuid("mem-a") is False

    def test_rejects_empty_string(self):
        assert _looks_like_uuid("") is False

    def test_rejects_non_string(self):
        assert _looks_like_uuid(None) is False
        assert _looks_like_uuid(123) is False


class TestResolveMemoryRefs:
    def test_passes_through_uuid(self):
        client = resolver_client()
        resolved, unresolved = _resolve_memory_refs(client, [UID_A], project=None)
        assert resolved == [UID_A]
        assert unresolved == []

    def test_canonicalizes_uppercase_uuid(self):
        client = resolver_client()
        resolved, unresolved = _resolve_memory_refs(client, [UID_A.upper()], project=None)
        assert resolved == [UID_A]
        assert unresolved == []

    def test_dedups_repeated_uuid(self):
        client = resolver_client()
        resolved, unresolved = _resolve_memory_refs(
            client, [UID_A, UID_A.upper(), UID_A], project=None
        )
        assert resolved == [UID_A]
        assert unresolved == []

    def test_resolves_name_via_db(self):
        client = resolver_client({"mem-a": UID_A})
        resolved, unresolved = _resolve_memory_refs(client, ["mem-a"], project=None)
        assert resolved == [UID_A]
        assert unresolved == []

    def test_unknown_name_goes_to_unresolved(self):
        client = resolver_client()
        resolved, unresolved = _resolve_memory_refs(client, ["ghost-name"], project=None)
        assert resolved == []
        assert unresolved == ["ghost-name"]

    def test_mixed_preserves_input_order(self):
        client = resolver_client({"mem-b": UID_B})
        resolved, unresolved = _resolve_memory_refs(
            client, [UID_A, "mem-b", UID_C], project=None
        )
        assert resolved == [UID_A, UID_B, UID_C]
        assert unresolved == []

    def test_scopes_lookup_by_project_when_provided(self):
        capture: list = []
        client = resolver_client({"mem-a": UID_A}, project_capture=capture)
        resolved, _ = _resolve_memory_refs(client, ["mem-a"], project="jarvis")
        assert resolved == [UID_A]
        assert capture == ["jarvis"]

    def test_no_project_scope_when_project_none(self):
        capture: list = []
        client = resolver_client({"mem-a": UID_A}, project_capture=capture)
        resolved, _ = _resolve_memory_refs(client, ["mem-a"], project=None)
        assert resolved == [UID_A]
        assert capture == []

    def test_skips_empty_and_non_string_refs(self):
        client = resolver_client()
        resolved, unresolved = _resolve_memory_refs(
            client, [None, 123, "", "   "], project=None
        )
        assert resolved == []
        assert unresolved == []

    def test_db_error_marks_name_unresolved(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.table.return_value.select.side_effect = RuntimeError("postgrest down")
        resolved, unresolved = _resolve_memory_refs(client, ["mem-a"], project=None)
        assert resolved == []
        assert unresolved == ["mem-a"]
