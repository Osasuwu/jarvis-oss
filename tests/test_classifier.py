"""Unit tests for mcp-memory/classifier.py — pure parsing + prompt assembly.

The HTTP path (classify_write) is exercised in integration tests; here we
cover the deterministic pieces that don't need network.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

# Stub httpx if not installed (mirrors test_memory_server.py setup).
try:
    import httpx  # noqa: F401
except ImportError:
    sys.modules["httpx"] = types.ModuleType("httpx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-memory"))

from classifier import (  # noqa: E402
    _build_user_message,
    _parse_response,
    _truncate,
    ClassifierDecision,
    MAX_NEIGHBOR_CONTENT_CHARS,
)


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_empty(self):
        assert _truncate("") == ""

    def test_truncates_with_ellipsis(self):
        text = "x" * (MAX_NEIGHBOR_CONTENT_CHARS + 100)
        out = _truncate(text)
        assert len(out) == MAX_NEIGHBOR_CONTENT_CHARS + 1  # +1 for the ellipsis char
        assert out.endswith("…")


# ---------------------------------------------------------------------------
# _build_user_message
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def test_includes_candidate_fields(self):
        cand = {
            "name": "test_memory", "type": "decision", "tags": ["a", "b"],
            "description": "test desc", "content": "test content",
        }
        out = _build_user_message(cand, [])
        assert "test_memory" in out
        assert "decision" in out
        assert "a, b" in out
        assert "test desc" in out
        assert "test content" in out

    def test_includes_neighbors(self):
        cand = {"name": "c", "type": "project"}
        nbrs = [
            {"id": "n1", "name": "neighbor1", "type": "project",
             "similarity": 0.823, "description": "older", "content": "older content"},
        ]
        out = _build_user_message(cand, nbrs)
        assert "n1" in out
        assert "neighbor1" in out
        assert "0.823" in out
        assert "older" in out

    def test_truncates_long_neighbor_content(self):
        cand = {"name": "c", "type": "project"}
        long_content = "y" * (MAX_NEIGHBOR_CONTENT_CHARS + 500)
        nbrs = [{"id": "n1", "name": "n", "type": "project",
                 "similarity": 0.8, "content": long_content}]
        out = _build_user_message(cand, nbrs)
        # Full content should NOT be present; truncated version should.
        assert long_content not in out
        assert "…" in out


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_clean_add(self):
        text = json.dumps({
            "decision": "ADD", "target_id": None,
            "confidence": 0.92, "reasoning": "novel",
        })
        d = _parse_response(text)
        assert d.decision == "ADD"
        assert d.target_id is None
        assert d.confidence == 0.92

    def test_clean_update(self):
        text = json.dumps({
            "decision": "UPDATE", "target_id": "abc-123",
            "confidence": 0.81, "reasoning": "refines",
        })
        d = _parse_response(text)
        assert d.decision == "UPDATE"
        assert d.target_id == "abc-123"

    def test_handles_prose_around_json(self):
        text = "Here's my decision:\n" + json.dumps({
            "decision": "NOOP", "target_id": None,
            "confidence": 0.7, "reasoning": "redundant",
        }) + "\n(end)"
        d = _parse_response(text)
        assert d.decision == "NOOP"

    def test_lowercase_decision_normalized(self):
        text = json.dumps({
            "decision": "delete", "target_id": "x",
            "confidence": 0.9, "reasoning": "",
        })
        d = _parse_response(text)
        assert d.decision == "DELETE"

    def test_invalid_decision_returns_none(self):
        text = json.dumps({
            "decision": "MAYBE", "target_id": None,
            "confidence": 0.9, "reasoning": "",
        })
        assert _parse_response(text) is None

    def test_update_without_target_downgrades_to_add(self):
        text = json.dumps({
            "decision": "UPDATE", "target_id": None,
            "confidence": 0.9, "reasoning": "",
        })
        d = _parse_response(text)
        assert d.decision == "ADD"
        assert d.confidence < 0.5  # penalty applied

    def test_string_null_target_normalized(self):
        text = json.dumps({
            "decision": "ADD", "target_id": "null",
            "confidence": 0.9, "reasoning": "",
        })
        d = _parse_response(text)
        assert d.target_id is None

    def test_confidence_clamped(self):
        text = json.dumps({
            "decision": "ADD", "target_id": None,
            "confidence": 1.5, "reasoning": "",
        })
        d = _parse_response(text)
        assert d.confidence == 1.0

    def test_garbage_returns_none(self):
        assert _parse_response("not json at all") is None
        assert _parse_response("") is None
        assert _parse_response("{broken json") is None

    def test_missing_confidence_defaults_safely(self):
        text = json.dumps({
            "decision": "ADD", "target_id": None, "reasoning": "",
        })
        d = _parse_response(text)
        assert 0 <= d.confidence <= 1
