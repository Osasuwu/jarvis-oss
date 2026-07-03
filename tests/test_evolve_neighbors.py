"""Unit tests for scripts/evolve-neighbors.py — parse + prompt assembly.

The HTTP path (call_haiku) and Supabase fetch paths are exercised in
integration tests or by manual smoke runs; here we cover the deterministic
pieces that don't need network or DB.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

# Stub httpx / supabase / dotenv if not installed so the module import works
# in minimal CI. They're only consulted by the HTTP/DB paths we don't test.
for name in ("httpx", "supabase", "dotenv"):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        if name == "supabase":
            mod.create_client = lambda *a, **k: None  # type: ignore[attr-defined]
        if name == "dotenv":
            mod.load_dotenv = lambda *a, **k: None  # type: ignore[attr-defined]
        sys.modules[name] = mod


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "evolve-neighbors.py"

# Hyphen in filename means we can't use normal `import evolve_neighbors`.
spec = importlib.util.spec_from_file_location("evolve_neighbors", SCRIPT_PATH)
assert spec and spec.loader
evo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evo)


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_unchanged(self):
        assert evo._truncate("hello") == "hello"

    def test_empty_string(self):
        assert evo._truncate("") == ""

    def test_none_passthrough(self):
        assert evo._truncate(None) == ""

    def test_truncates_with_ellipsis(self):
        text = "x" * (evo.MAX_CONTENT_CHARS + 100)
        out = evo._truncate(text)
        assert out.endswith("…")
        assert len(out) == evo.MAX_CONTENT_CHARS + 1  # +1 for the ellipsis char

    def test_at_boundary_no_truncation(self):
        text = "x" * evo.MAX_CONTENT_CHARS
        out = evo._truncate(text)
        assert out == text
        assert "…" not in out

    def test_one_past_boundary_truncates(self):
        text = "x" * (evo.MAX_CONTENT_CHARS + 1)
        out = evo._truncate(text)
        assert out.endswith("…")
        assert len(out) == evo.MAX_CONTENT_CHARS + 1


# ---------------------------------------------------------------------------
# _parse_response — Haiku output parsing with defensive downgrades
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_garbage_returns_none(self):
        assert evo._parse_response("this is not json", {"x"}) is None

    def test_missing_proposals_key_returns_none(self):
        assert evo._parse_response('{"foo": 1}', {"x"}) is None

    def test_empty_proposals_list(self):
        assert evo._parse_response('{"proposals": []}', {"x"}) == []

    def test_valid_update_tags(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_TAGS", '
            '"new_tags": ["a", "b"], "confidence": 0.88, "reasoning": "tags stale"}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r is not None
        assert len(r) == 1
        assert r[0]["action"] == "UPDATE_TAGS"
        assert r[0]["new_tags"] == ["a", "b"]
        assert r[0]["new_description"] is None
        assert r[0]["confidence"] == 0.88
        assert r[0]["reasoning"] == "tags stale"

    def test_update_desc_strips_description(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_DESC", '
            '"new_description": "  a new line.  ", "confidence": 0.8}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "UPDATE_DESC"
        assert r[0]["new_description"] == "a new line."
        assert r[0]["new_tags"] is None

    def test_update_tags_without_tags_downgrades_to_keep(self):
        # Contradictory output: action says update tags but no tags provided.
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_TAGS", '
            '"new_tags": null, "confidence": 0.7}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "KEEP"
        assert "downgraded" in r[0]["reasoning"]

    def test_update_both_without_desc_downgrades_to_update_tags(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_BOTH", '
            '"new_tags": ["t1"], "new_description": null, "confidence": 0.9}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "UPDATE_TAGS"
        assert r[0]["new_tags"] == ["t1"]
        assert r[0]["new_description"] is None

    def test_update_both_without_tags_downgrades_to_update_desc(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_BOTH", '
            '"new_tags": null, "new_description": "d", "confidence": 0.9}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "UPDATE_DESC"
        assert r[0]["new_description"] == "d"

    def test_hallucinated_neighbor_id_dropped(self):
        text = '{"proposals": [{"neighbor_id": "fake-999", "action": "KEEP"}]}'
        r = evo._parse_response(text, {"abc-123"})
        # fake-999 is not in the known set → filter out; nothing left.
        assert r == []

    def test_unknown_action_becomes_keep(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "DELETE_IT", '
            '"confidence": 0.8}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "KEEP"

    def test_confidence_clamped_to_unit_interval(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": 2.5}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["confidence"] == 1.0

        text_neg = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": -0.3}]}'
        )
        r = evo._parse_response(text_neg, {"abc-123"})
        assert r and r[0]["confidence"] == 0.0

    def test_tolerates_prose_around_json(self):
        text = (
            "Sure, here's my assessment:\n\n"
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": 0.9}]}\n\n'
            "Let me know if you need more."
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "KEEP"

    def test_non_string_tag_items_reject_all_tags(self):
        # Haiku occasionally emits mixed-type arrays — treat as invalid so we
        # never write partial tag lists.
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_TAGS", '
            '"new_tags": ["a", 42, null], "confidence": 0.9}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "KEEP"
        assert "downgraded" in r[0]["reasoning"]

    def test_strips_whitespace_tags(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_TAGS", '
            '"new_tags": ["  tag1  ", "", "tag2"], "confidence": 0.9}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "UPDATE_TAGS"
        assert r[0]["new_tags"] == ["tag1", "tag2"]

    def test_empty_description_downgrades(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_DESC", '
            '"new_description": "   ", "confidence": 0.7}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["action"] == "KEEP"

    def test_multiple_proposals_filtered_individually(self):
        text = (
            '{"proposals": ['
            '{"neighbor_id": "a", "action": "KEEP", "confidence": 0.9},'
            '{"neighbor_id": "not-real", "action": "UPDATE_TAGS", "new_tags": ["x"]},'
            '{"neighbor_id": "b", "action": "UPDATE_TAGS", "new_tags": ["y"], "confidence": 0.7}'
            ']}'
        )
        r = evo._parse_response(text, {"a", "b"})
        assert len(r) == 2  # not-real dropped
        by_id = {p["neighbor_id"]: p for p in r}
        assert by_id["a"]["action"] == "KEEP"
        assert by_id["b"]["action"] == "UPDATE_TAGS"

    def test_null_reasoning_becomes_empty_string(self):
        # Regression: str(None) == "None" was leaking the literal "None" into
        # markdown/JSON output. Treat JSON null / non-strings as empty.
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": 0.9, "reasoning": null}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["reasoning"] == ""

    def test_non_string_reasoning_becomes_empty_string(self):
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": 0.9, "reasoning": 42}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r and r[0]["reasoning"] == ""

    # ------------------------------------------------------------------
    # Property-style invariants — these hold for any well-structured input
    # ------------------------------------------------------------------

    def test_never_raises_on_malformed_input(self):
        """Defensive: any string input must not raise an exception."""
        examples = [
            "",
            "not json",
            "{",
            "}",
            "{{}",
            "[]",
            "null",
            "true",
            "42",
            '"string"',
            "\x00\x01\x02",
            "\n\n\n\n",
            " " * 100,
            '{"broken": }',
            "{'single_quotes': 'not valid json'}",
        ]
        for text in examples:
            # Must not raise
            evo._parse_response(text, {"x"})

    def test_return_value_is_always_valid_shape(self):
        """Result is always None, [], or list of dicts with 6 expected keys."""
        valid_ids = {"abc-123", "def-456"}
        # (input_text, neighbor_ids, expected_length_or_None)
        examples = [
            ("garbage", valid_ids, None),                     # unparseable → None
            ('{"proposals": []}', valid_ids, 0),               # empty list → []
            (
                '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
                '"confidence": 0.9}]}',
                valid_ids,
                1,
            ),
        ]
        for text, ids, expected in examples:
            r = evo._parse_response(text, ids)
            if expected is None:
                assert r is None
            else:
                assert isinstance(r, list)
                assert len(r) == expected
                for p in r:
                    assert isinstance(p, dict)
                    assert set(p.keys()) == {
                        "neighbor_id", "action", "new_tags",
                        "new_description", "confidence", "reasoning",
                    }

    def test_all_returned_proposals_have_valid_action(self):
        """Action in every proposal must be one of VALID_ACTIONS."""
        text = (
            '{"proposals": ['
            '{"neighbor_id": "a", "action": "KEEP", "confidence": 0.9},'
            '{"neighbor_id": "b", "action": "DELETE", "confidence": 0.8},'
            '{"neighbor_id": "c", "action": "  update_tags  ", "new_tags": ["x"]},'
            '{"neighbor_id": "d", "action": "", "confidence": 0.5}'
            "]}"
        )
        r = evo._parse_response(text, {"a", "b", "c", "d"})
        assert r is not None
        for p in r:
            assert p["action"] in evo.VALID_ACTIONS

    def test_confidence_always_clamped_to_unit_interval(self):
        """Confidence always in [0.0, 1.0] regardless of input."""
        text = (
            '{"proposals": ['
            '{"neighbor_id": "a", "action": "KEEP", "confidence": 100},'
            '{"neighbor_id": "b", "action": "KEEP", "confidence": -1},'
            '{"neighbor_id": "c", "action": "KEEP", "confidence": null},'
            '{"neighbor_id": "d", "action": "KEEP"},'
            '{"neighbor_id": "e", "action": "KEEP", "confidence": 0.5}'
            "]}"
        )
        r = evo._parse_response(text, {"a", "b", "c", "d", "e"})
        assert r is not None
        for p in r:
            assert 0.0 <= p["confidence"] <= 1.0

    def test_hallucinated_ids_always_dropped(self):
        """Proposals with unknown neighbor_ids never appear in output."""
        r = evo._parse_response(
            '{"proposals": [{"neighbor_id": "unknown-1"}, {"neighbor_id": "unknown-2"}]}',
            {"known-id"},
        )
        assert r == []

    def test_extra_top_level_keys_tolerated(self):
        """Unknown top-level keys do not interfere with parsing."""
        text = (
            '{"proposals": [{"neighbor_id": "a", "action": "KEEP", '
            '"confidence": 0.9}], "unexpected_key": "value", "another": [1, 2, 3]}'
        )
        r = evo._parse_response(text, {"a"})
        assert r is not None
        assert len(r) == 1
        assert r[0]["action"] == "KEEP"

    def test_extra_fields_in_proposal_tolerated(self):
        """Proposals with extra fields are still parsed and returned."""
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": 0.9, "extra_field": "ignored", "nested": {"x": 1}}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r is not None
        assert r[0]["action"] == "KEEP"
        # Extra fields are dropped from output — only the 6 expected keys survive.
        assert set(r[0].keys()) == {
            "neighbor_id", "action", "new_tags",
            "new_description", "confidence", "reasoning",
        }

    def test_missing_neighbor_id_field_skipped(self):
        """A proposal dict without neighbor_id is skipped."""
        text = '{"proposals": [{"action": "UPDATE_TAGS", "confidence": 0.9}]}'
        r = evo._parse_response(text, {"abc-123"})
        assert r == []

    def test_confidence_as_numeric_string(self):
        """Confidence passed as a string number is accepted by float()."""
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": "0.85"}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r is not None
        assert r[0]["confidence"] == 0.85

    def test_reasoning_whitespace_only_becomes_empty(self):
        """Reasoning with only whitespace is stripped to empty string."""
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "KEEP", '
            '"confidence": 0.9, "reasoning": "   "}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r is not None
        assert r[0]["reasoning"] == ""

    def test_non_dict_proposal_skipped(self):
        """Non-dict items in the proposals list are skipped."""
        text = (
            '{"proposals": ['
            '"string proposal", 42, null, '
            '{"neighbor_id": "abc-123", "action": "KEEP", "confidence": 0.9}'
            "]}"
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r is not None
        assert len(r) == 1
        assert r[0]["neighbor_id"] == "abc-123"

    def test_unicode_chars_in_strings_tolerated(self):
        """Unicode/special characters in JSON string values are handled."""
        text = (
            '{"proposals": [{"neighbor_id": "abc-123", "action": "UPDATE_TAGS", '
            '"new_tags": ["émoji🔥", "статус", "中文"], "confidence": 0.9}]}'
        )
        r = evo._parse_response(text, {"abc-123"})
        assert r is not None
        assert r[0]["action"] == "UPDATE_TAGS"
        assert r[0]["new_tags"] == ["émoji🔥", "статус", "中文"]


# ---------------------------------------------------------------------------
# _fallback_keep — last-resort safe output when the API fails
# ---------------------------------------------------------------------------


class TestFallbackKeep:
    def test_returns_keep_for_every_neighbor(self):
        neighbors = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        out = evo._fallback_keep(neighbors, "test-reason")
        assert len(out) == 3
        for p in out:
            assert p["action"] == "KEEP"
            assert p["confidence"] == 0.0
            assert p["new_tags"] is None
            assert p["new_description"] is None
            assert "fallback" in p["reasoning"]
            assert "test-reason" in p["reasoning"]

    def test_empty_input(self):
        assert evo._fallback_keep([], "no-op") == []

    def test_all_returned_proposals_have_expected_schema(self):
        """Every returned proposal has all 6 fields."""
        neighbors = [{"id": "a"}, {"id": "b"}]
        out = evo._fallback_keep(neighbors, "test")
        for p in out:
            assert set(p.keys()) == {
                "neighbor_id", "action", "new_tags",
                "new_description", "confidence", "reasoning",
            }
            assert p["action"] == "KEEP"
            assert p["new_tags"] is None
            assert p["new_description"] is None
            assert p["confidence"] == 0.0
            assert "test" in p["reasoning"]


# ---------------------------------------------------------------------------
# call_haiku — fallback contract when ANTHROPIC_API_KEY is missing
# ---------------------------------------------------------------------------


class TestCallHaikuFallback:
    def test_missing_api_key_returns_fallback_keeps(self, monkeypatch):
        # main() no longer hard-exits when the key is absent; it relies on
        # call_haiku() collapsing to a safe KEEP-only plan. Pin that contract
        # so future refactors don't regress it.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        neighbors = [{"id": "n1"}, {"id": "n2"}]
        out = evo.call_haiku(
            {"id": "old"}, {"id": "new"}, neighbors,
            model="test", timeout=1.0,
        )
        assert len(out) == 2
        assert all(p["action"] == "KEEP" for p in out)
        assert all(p["confidence"] == 0.0 for p in out)
        assert all("ANTHROPIC_API_KEY missing" in p["reasoning"] for p in out)

    def test_empty_neighbors_without_key_returns_empty(self, monkeypatch):
        # No neighbors → nothing to plan, regardless of key.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = evo.call_haiku(
            {"id": "old"}, {"id": "new"}, [],
            model="test", timeout=1.0,
        )
        assert out == []


# ---------------------------------------------------------------------------
# build_user_message — prompt assembly
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def _old(self) -> dict:
        return {
            "id": "old-id",
            "name": "old_memory",
            "type": "decision",
            "tags": ["t1", "t2"],
            "description": "Old description",
            "content": "Old content body",
        }

    def _new(self) -> dict:
        return {
            "id": "new-id",
            "name": "new_memory",
            "type": "decision",
            "tags": ["t3"],
            "description": "New description",
            "content": "New content body",
        }

    def test_includes_both_memories_and_neighbors(self):
        neighbors = [
            {
                "id": "n1",
                "name": "neighbor_one",
                "type": "project",
                "tags": ["t"],
                "description": "Neighbor one",
                "link_type": "related",
            },
            {
                "id": "n2",
                "name": "neighbor_two",
                "type": "reference",
                "tags": [],
                "description": "",
                "link_type": "supersedes",
            },
        ]
        msg = evo.build_user_message(self._old(), self._new(), neighbors)
        assert "OLD_MEMORY" in msg
        assert "NEW_MEMORY" in msg
        assert "old_memory" in msg
        assert "new_memory" in msg
        assert "neighbor_one" in msg
        assert "neighbor_two" in msg
        assert "NEIGHBOR 1" in msg
        assert "NEIGHBOR 2" in msg

    def test_no_neighbors_still_renders_both_sides(self):
        msg = evo.build_user_message(self._old(), self._new(), [])
        assert "OLD_MEMORY" in msg
        assert "NEW_MEMORY" in msg
        assert "NEIGHBOR" not in msg

    def test_truncates_long_content(self):
        long_old = self._old()
        long_old["content"] = "x" * (evo.MAX_CONTENT_CHARS + 500)
        msg = evo.build_user_message(long_old, self._new(), [])
        # Message must contain the ellipsis marker, proving truncation fired
        # before it hit the Haiku prompt.
        assert "…" in msg

    def test_no_tags_rendered_as_none(self):
        neighbors = [
            {"id": "n1", "name": "bare", "type": "project", "tags": [], "description": ""}
        ]
        msg = evo.build_user_message(self._old(), self._new(), neighbors)
        assert "tags: (none)" in msg


# ---------------------------------------------------------------------------
# render_markdown — table formatting edge cases
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def _result(self, reasoning: str) -> dict:
        return {
            "queue_id": "queue-abc-123",
            "applied_at": "2026-04-19T10:00:00+00:00",
            "old_memory": {"id": "old", "name": "old_mem", "type": "decision", "tags": []},
            "new_memory": {"id": "new", "name": "new_mem", "type": "decision", "tags": []},
            "neighbors": [{"id": "n1", "name": "n1_name", "tags": [], "description": ""}],
            "proposals": [
                {
                    "neighbor_id": "n1",
                    "action": "KEEP",
                    "new_tags": None,
                    "new_description": None,
                    "confidence": 0.9,
                    "reasoning": reasoning,
                }
            ],
        }

    def test_newlines_in_reasoning_do_not_break_table(self):
        # Reasoning with \n would split the markdown row across lines and
        # corrupt the table. Must be flattened to single-line.
        out = evo.render_markdown(
            [self._result("line one\nline two\r\nline three")],
            model="test", limit=10,
        )
        # Find the table rows (skip header + separator)
        body_rows = [
            ln for ln in out.splitlines()
            if ln.startswith("| `n1_name`")
        ]
        assert len(body_rows) == 1
        row = body_rows[0]
        assert "line one line two line three" in row
        assert "\n" not in row  # splitlines guarantees this, but explicit

    def test_pipe_in_reasoning_still_escaped(self):
        # Pre-existing guard — don't regress it.
        out = evo.render_markdown(
            [self._result("has | pipe")],
            model="test", limit=10,
        )
        assert "has \\| pipe" in out


# ---------------------------------------------------------------------------
# Phase 5.2-β apply-path helpers
# ---------------------------------------------------------------------------


class TestActionableProposals:
    def test_keeps_filtered_out(self):
        proposals = [
            {"action": "KEEP"},
            {"action": "UPDATE_TAGS", "new_tags": ["x"]},
            {"action": "UPDATE_DESC", "new_description": "d"},
            {"action": "UPDATE_BOTH", "new_tags": ["y"], "new_description": "d2"},
        ]
        out = evo._actionable_proposals(proposals)
        assert [p["action"] for p in out] == ["UPDATE_TAGS", "UPDATE_DESC", "UPDATE_BOTH"]

    def test_unknown_actions_filtered_out(self):
        proposals = [{"action": "DELETE"}, {"action": "UPDATE_TAGS", "new_tags": ["a"]}]
        out = evo._actionable_proposals(proposals)
        assert [p["action"] for p in out] == ["UPDATE_TAGS"]


class TestPlanMinConfidence:
    def test_empty_returns_one(self):
        assert evo._plan_min_confidence([]) == 1.0

    def test_min_across_proposals(self):
        proposals = [
            {"action": "UPDATE_TAGS", "confidence": 0.9},
            {"action": "UPDATE_DESC", "confidence": 0.4},
            {"action": "UPDATE_BOTH", "confidence": 0.7},
        ]
        assert evo._plan_min_confidence(proposals) == 0.4

    def test_missing_confidence_treated_as_zero(self):
        proposals = [{"action": "UPDATE_TAGS"}]
        assert evo._plan_min_confidence(proposals) == 0.0


class TestBuildRpcPlan:
    def _result(self, proposals: list[dict]) -> dict:
        return {
            "queue_id": "update-q-1",
            "applied_at": "2026-04-19T10:00:00+00:00",
            "old_memory": {"id": "old-id", "name": "old"},
            "new_memory": {"id": "new-id", "name": "new"},
            "neighbors": [],
            "proposals": proposals,
        }

    def test_excludes_keep_from_payload(self):
        result = self._result([
            {"neighbor_id": "n1", "action": "KEEP", "confidence": 0.9},
            {
                "neighbor_id": "n2", "action": "UPDATE_TAGS",
                "new_tags": ["a"], "confidence": 0.9, "reasoning": "stale tag",
            },
        ])
        plan = evo._build_rpc_plan(result, "skill:evolution:test:2026-04-19")
        assert plan["decision"] == "EVOLVE"
        assert plan["update_queue_id"] == "update-q-1"
        assert plan["candidate_id"] == "new-id"
        assert plan["target_id"] == "old-id"
        assert plan["source_provenance"] == "skill:evolution:test:2026-04-19"
        assert len(plan["proposals"]) == 1
        assert plan["proposals"][0]["neighbor_id"] == "n2"
        assert plan["proposals"][0]["new_tags"] == ["a"]

    def test_preserves_action_specific_fields(self):
        result = self._result([
            {
                "neighbor_id": "n1", "action": "UPDATE_DESC",
                "new_tags": None, "new_description": "fresh",
                "confidence": 0.88, "reasoning": "desc drifted",
            },
            {
                "neighbor_id": "n2", "action": "UPDATE_BOTH",
                "new_tags": ["x", "y"], "new_description": "desc2",
                "confidence": 0.92,
            },
        ])
        plan = evo._build_rpc_plan(result, "skill:evolution:test:2026-04-19")
        by_id = {p["neighbor_id"]: p for p in plan["proposals"]}
        assert by_id["n1"]["new_description"] == "fresh"
        assert by_id["n1"]["new_tags"] is None
        assert by_id["n2"]["new_tags"] == ["x", "y"]
        assert by_id["n2"]["new_description"] == "desc2"


# ---------------------------------------------------------------------------
# apply_or_queue — routing based on confidence gate
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, data):
        self.data = data


class _FakeRPC:
    def __init__(self, parent, name, params):
        self.parent = parent
        self.name = name
        self.params = params

    def execute(self):
        self.parent.rpc_calls.append({"name": self.name, "params": self.params})
        return _FakeResp(self.parent.rpc_return)


class _FakeTableQuery:
    def __init__(self, parent, table):
        self.parent = parent
        self.table_name = table
        self.op = None
        self.row = None

    def insert(self, row):
        self.op = "insert"
        self.row = row
        return self

    def execute(self):
        self.parent.table_calls.append({
            "table": self.table_name, "op": self.op, "row": self.row,
        })
        return _FakeResp([{"id": self.parent.next_queue_id}])


class _FakeClient:
    """Minimal stand-in for the supabase-py client covering .rpc / .table.insert.

    Every call is recorded on the instance so tests can assert on routing.
    """

    def __init__(self, *, rpc_return=None, next_queue_id: str = "q-fake"):
        self.rpc_calls: list[dict] = []
        self.table_calls: list[dict] = []
        self.rpc_return = rpc_return or {
            "status": "applied",
            "applied_count": 2,
            "queue_id": "applied-q-fake",
            "decision": "EVOLVE",
        }
        self.next_queue_id = next_queue_id

    def rpc(self, name, params):
        return _FakeRPC(self, name, params)

    def table(self, name):
        return _FakeTableQuery(self, name)


class TestApplyOrQueue:
    def _result(self, queue_id: str, proposals: list[dict]) -> dict:
        return {
            "queue_id": queue_id,
            "applied_at": "2026-04-19T10:00:00+00:00",
            "old_memory": {"id": f"old-{queue_id}", "name": "old"},
            "new_memory": {"id": f"new-{queue_id}", "name": "new"},
            "neighbors": [],
            "proposals": proposals,
        }

    def test_all_keep_skipped(self):
        client = _FakeClient()
        results = [self._result("u1", [{
            "neighbor_id": "n1", "action": "KEEP", "confidence": 0.9,
        }])]
        outcomes = evo.apply_or_queue(client, results, model="test", gate=0.85)
        assert outcomes[0]["status"] == "skipped_all_keep"
        assert not client.rpc_calls
        assert not client.table_calls

    def test_high_confidence_routes_to_apply(self):
        client = _FakeClient()
        results = [self._result("u1", [{
            "neighbor_id": "n1", "action": "UPDATE_TAGS",
            "new_tags": ["fresh"], "confidence": 0.92, "reasoning": "ok",
        }])]
        outcomes = evo.apply_or_queue(client, results, model="test", gate=0.85)
        assert outcomes[0]["status"] == "applied"
        assert outcomes[0]["queue_id"] == "applied-q-fake"
        assert len(client.rpc_calls) == 1
        rpc = client.rpc_calls[0]
        assert rpc["name"] == "apply_evolution_plan"
        assert rpc["params"]["plan"]["decision"] == "EVOLVE"
        assert rpc["params"]["queue_meta"]["status"] == "auto_applied"
        assert not client.table_calls

    def test_low_confidence_routes_to_queue(self):
        client = _FakeClient(next_queue_id="queued-q-fake")
        results = [self._result("u1", [{
            "neighbor_id": "n1", "action": "UPDATE_TAGS",
            "new_tags": ["x"], "confidence": 0.5,
        }])]
        outcomes = evo.apply_or_queue(client, results, model="test", gate=0.85)
        assert outcomes[0]["status"] == "queued"
        assert outcomes[0]["queue_id"] == "queued-q-fake"
        assert not client.rpc_calls
        assert len(client.table_calls) == 1
        call = client.table_calls[0]
        assert call["table"] == "memory_review_queue"
        assert call["row"]["decision"] == "EVOLVE"
        assert call["row"]["status"] == "pending"
        # Low-conf plan still carries the full payload so a later owner
        # review can act on it.
        assert call["row"]["evolution_payload"]["update_queue_id"] == "u1"
        assert len(call["row"]["evolution_payload"]["proposals"]) == 1

    def test_mixed_confidence_uses_minimum(self):
        # One proposal at 0.95, another at 0.4 — min=0.4, gate=0.85 → queue.
        client = _FakeClient()
        results = [self._result("u1", [
            {"neighbor_id": "n1", "action": "UPDATE_TAGS",
             "new_tags": ["a"], "confidence": 0.95},
            {"neighbor_id": "n2", "action": "UPDATE_DESC",
             "new_description": "d", "confidence": 0.4},
        ])]
        outcomes = evo.apply_or_queue(client, results, model="test", gate=0.85)
        assert outcomes[0]["status"] == "queued"
        assert outcomes[0]["min_confidence"] == 0.4
        assert not client.rpc_calls
        assert len(client.table_calls) == 1

    def test_gate_exactly_at_min_confidence_applies(self):
        # Gate is inclusive on the lower bound.
        client = _FakeClient()
        results = [self._result("u1", [{
            "neighbor_id": "n1", "action": "UPDATE_TAGS",
            "new_tags": ["a"], "confidence": 0.85,
        }])]
        outcomes = evo.apply_or_queue(client, results, model="test", gate=0.85)
        assert outcomes[0]["status"] == "applied"

    def test_batch_error_does_not_abort_remaining(self):
        # First plan's RPC raises — outcome should be 'error'; second plan
        # (also RPC-bound) should still proceed.
        class FlakyClient(_FakeClient):
            def __init__(self):
                super().__init__()
                self.rpc_count = 0

            def rpc(self, name, params):
                self.rpc_count += 1
                if self.rpc_count == 1:
                    class Bomb:
                        def execute(self_inner):
                            raise RuntimeError("DB unreachable")
                    return Bomb()
                return _FakeRPC(self, name, params)

        client = FlakyClient()
        results = [
            self._result("u1", [{
                "neighbor_id": "n1", "action": "UPDATE_TAGS",
                "new_tags": ["a"], "confidence": 0.95,
            }]),
            self._result("u2", [{
                "neighbor_id": "n2", "action": "UPDATE_TAGS",
                "new_tags": ["b"], "confidence": 0.95,
            }]),
        ]
        outcomes = evo.apply_or_queue(client, results, model="test", gate=0.85)
        assert outcomes[0]["status"] == "error"
        assert "DB unreachable" in outcomes[0]["error"]
        assert outcomes[1]["status"] == "applied"


# ---------------------------------------------------------------------------
# _fetch_seen_update_ids — server-side dedup filter
# ---------------------------------------------------------------------------


class _FakeDedupQuery:
    """Records the filter chain .select().eq().filter() for assertion."""

    def __init__(self, parent):
        self.parent = parent
        self.selects: list[str] = []
        self.eqs: list[tuple] = []
        self.filters: list[tuple] = []

    def select(self, cols):
        self.selects.append(cols)
        return self

    def eq(self, col, val):
        self.eqs.append((col, val))
        return self

    def filter(self, col, op, val):
        self.filters.append((col, op, val))
        return self

    def execute(self):
        if self.parent.raise_on_execute:
            raise RuntimeError(self.parent.raise_on_execute)
        self.parent.last_query = self
        return _FakeResp(self.parent.response_rows)


class _FakeDedupClient:
    def __init__(self, *, response_rows=None, raise_on_execute: str | None = None):
        self.response_rows = response_rows or []
        self.raise_on_execute = raise_on_execute
        self.last_query: _FakeDedupQuery | None = None

    def table(self, _name):
        return _FakeDedupQuery(self)


class TestFetchSeenUpdateIds:
    def test_empty_input_short_circuits(self):
        client = _FakeDedupClient()
        assert evo._fetch_seen_update_ids(client, []) == set()
        assert client.last_query is None  # no DB call

    def test_server_side_filter_uses_index_expression(self):
        # Two already-evolved rows out of three candidates.
        client = _FakeDedupClient(response_rows=[
            {"evolution_payload": {"update_queue_id": "u1"}},
            {"evolution_payload": {"update_queue_id": "u3"}},
        ])
        seen = evo._fetch_seen_update_ids(client, ["u1", "u2", "u3"])
        assert seen == {"u1", "u3"}
        q = client.last_query
        # decision=EVOLVE eq stays
        assert ("decision", "EVOLVE") in q.eqs
        # Critical: filter must target the JSON-path key that the functional
        # index indexes. Any regression back to client-side filtering would
        # drop this and scan the full EVOLVE audit set.
        assert len(q.filters) == 1
        col, op, val = q.filters[0]
        assert col == "evolution_payload->>update_queue_id"
        assert op == "in"
        assert val.startswith("(") and val.endswith(")")
        assert '"u1"' in val and '"u2"' in val and '"u3"' in val

    def test_db_error_fails_open(self, capsys):
        # Dedup is an optimization — a DB blip must not stop planning.
        client = _FakeDedupClient(raise_on_execute="db down")
        assert evo._fetch_seen_update_ids(client, ["u1"]) == set()
        err = capsys.readouterr().err
        assert "dedup lookup failed" in err
