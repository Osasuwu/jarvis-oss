"""Unit tests for the deterministic helpers in scripts/migrate-memory-structure.py.

The Haiku call + Supabase writes are integration-only (smoke-tested via
`--dry-run` on a real DB). Here we pin the detection, validation, JSON
parsing, and provenance-heuristic contracts.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "migrate-memory-structure.py"

spec = importlib.util.spec_from_file_location("migrate_memory_structure", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules["migrate_memory_structure"] = module
spec.loader.exec_module(module)


has_required_structure = module.has_required_structure
validate_rewrite = module.validate_rewrite
_parse_json_response = module._parse_json_response
heuristic_provenance = module.heuristic_provenance


class TestHasRequiredStructure:
    def test_both_present_returns_true(self):
        text = "Rule: do X.\n\n**Why:** reason.\n\n**How to apply:** when Y."
        assert has_required_structure(text) is True

    def test_missing_why_returns_false(self):
        text = "Rule.\n\n**How to apply:** when Y."
        assert has_required_structure(text) is False

    def test_missing_how_returns_false(self):
        text = "Rule.\n\n**Why:** reason."
        assert has_required_structure(text) is False

    def test_neither_returns_false(self):
        assert has_required_structure("just a paragraph") is False

    def test_empty_returns_false(self):
        assert has_required_structure("") is False

    def test_none_returns_false(self):
        assert has_required_structure(None) is False

    def test_case_insensitive(self):
        text = "Rule.\n\n**why:** reason.\n\n**HOW TO APPLY:** where."
        assert has_required_structure(text) is True

    def test_with_or_without_trailing_colon(self):
        text = "Rule.\n\n**Why** reason.\n\n**How to apply** where."
        assert has_required_structure(text) is True

    def test_inline_words_do_not_count(self):
        text = "We ask why and when to apply it. Just a sentence."
        assert has_required_structure(text) is False


class TestValidateRewrite:
    def test_ok_same_length(self):
        original = "x" * 200
        rewritten = "Rule.\n\n**Why:** " + "y" * 90 + "\n\n**How to apply:** " + "z" * 90
        ok, reason = validate_rewrite(original, rewritten)
        assert ok is True
        assert reason == "ok"

    def test_reject_missing_why(self):
        ok, reason = validate_rewrite("orig", "Rule.\n\n**How to apply:** x")
        assert ok is False
        assert reason == "missing_why_section"

    def test_reject_missing_how(self):
        ok, reason = validate_rewrite("orig", "Rule.\n\n**Why:** x")
        assert ok is False
        assert reason == "missing_how_section"

    def test_reject_empty(self):
        ok, reason = validate_rewrite("orig", None)
        assert ok is False
        assert reason == "empty_output"

    def test_reject_length_collapse(self):
        original = "x" * 500
        rewritten = "**Why:** y\n**How to apply:** z"
        ok, reason = validate_rewrite(original, rewritten)
        assert ok is False
        assert reason.startswith("length_collapsed_ratio=")

    def test_reject_length_bloat(self):
        original = "Rule."
        rewritten = "Rule.\n\n**Why:** " + ("y" * 500) + "\n\n**How to apply:** " + ("z" * 500)
        ok, reason = validate_rewrite(original, rewritten)
        assert ok is False
        assert reason.startswith("length_bloated_ratio=")

    def test_empty_original_accepted(self):
        rewritten = "**Why:** reason.\n**How to apply:** here."
        ok, reason = validate_rewrite("", rewritten)
        assert ok is True
        assert reason == "ok_empty_original"


class TestParseJsonResponse:
    def test_plain_json(self):
        assert _parse_json_response('{"a": 1}') == {"a": 1}

    def test_whitespace_ok(self):
        assert _parse_json_response('  {"a": 1}  ') == {"a": 1}

    def test_fenced_json_block(self):
        text = '```json\n{"a": 1}\n```'
        assert _parse_json_response(text) == {"a": 1}

    def test_fenced_plain_block(self):
        text = '```\n{"a": 1}\n```'
        assert _parse_json_response(text) == {"a": 1}

    def test_json_embedded_in_prose(self):
        text = "Here you go: {\"a\": 1, \"b\": \"x\"}. Done."
        parsed = _parse_json_response(text)
        assert parsed == {"a": 1, "b": "x"}

    def test_empty_returns_none(self):
        assert _parse_json_response("") is None

    def test_none_returns_none(self):
        assert _parse_json_response(None) is None

    def test_malformed_returns_none(self):
        assert _parse_json_response("not json at all") is None

    def test_multiline_content_preserved(self):
        text = '{"rewritten_content": "line1\\nline2", "confidence": 0.8}'
        parsed = _parse_json_response(text)
        assert parsed["rewritten_content"] == "line1\nline2"
        assert parsed["confidence"] == 0.8


class TestHeuristicProvenance:
    def test_iso_with_tz(self):
        assert heuristic_provenance("2026-03-15T10:22:33+00:00") == "session:2026-03-15"

    def test_iso_zulu(self):
        assert heuristic_provenance("2026-03-15T10:22:33Z") == "session:2026-03-15"

    def test_date_only(self):
        assert heuristic_provenance("2026-03-15") == "session:2026-03-15"

    def test_none_returns_none(self):
        assert heuristic_provenance(None) is None

    def test_empty_returns_none(self):
        assert heuristic_provenance("") is None

    def test_unparseable_returns_none(self):
        assert heuristic_provenance("yesterday") is None

    def test_respects_utc_offset(self):
        # 23:59 UTC+5 = 18:59 UTC, same calendar date in heuristic (we use the
        # timestamp's own date field, not converted).
        assert heuristic_provenance("2026-03-15T23:59:00+05:00") == "session:2026-03-15"


class TestFetchPassBQueryShape:
    """Smoke-test query predicates via a recording fake client."""

    def test_predicates_match_spec(self):
        seen = {"filters": [], "order": None, "limit": None}

        class _Q:
            def __init__(self, seen):
                self.seen = seen

            def select(self, *a, **k):
                return self

            def eq(self, col, val):
                self.seen["filters"].append(("eq", col, val))
                return self

            def lt(self, col, val):
                self.seen["filters"].append(("lt", col, val))
                return self

            def is_(self, col, val):
                self.seen["filters"].append(("is", col, val))
                return self

            def order(self, col, desc=False):
                self.seen["order"] = (col, desc)
                return self

            def limit(self, n):
                self.seen["limit"] = n
                return self

            def execute(self):
                class R:
                    data = []

                return R

        class _Client:
            def __init__(self, seen):
                self.seen = seen

            def table(self, _name):
                return _Q(self.seen)

        client = _Client(seen)
        module.fetch_pass_b_candidates(client, limit=10)

        filters = set((k, c, v) for (k, c, v) in seen["filters"])
        assert ("eq", "source_provenance", module.LEGACY_PROVENANCE_SENTINEL) in filters
        assert ("lt", "created_at", module.PROVENANCE_CUTOFF_DATE) in filters
        assert seen["limit"] == 10


class TestFetchPassACandidates:
    """Post-filter drops rows already containing Why/How-to-apply markers."""

    def test_already_structured_rows_filtered_out(self):
        rows = [
            {"id": "a", "name": "good", "content": "**Why:** x\n**How to apply:** y"},
            {"id": "b", "name": "bad", "content": "just a paragraph"},
        ]

        class _Q:
            def __init__(self, rows):
                self.rows = rows

            def select(self, *a, **k):
                return self

            def in_(self, *a, **k):
                return self

            def is_(self, *a, **k):
                return self

            def order(self, *a, **k):
                return self

            def limit(self, _n):
                return self

            def execute(self):
                class R:
                    def __init__(self, data):
                        self.data = data

                return R(self.rows)

        class _Client:
            def __init__(self, rows):
                self.rows = rows

            def table(self, _name):
                return _Q(self.rows)

        client = _Client(rows)
        candidates = module.fetch_pass_a_candidates(client, limit=None)
        names = [c["name"] for c in candidates]
        assert "bad" in names
        assert "good" not in names
