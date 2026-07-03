"""Unit tests for mcp-memory/server.py — pure functions only.

No external dependencies: RRF merge, temporal scoring, formatting,
cosine similarity, pgvector parsing, excluded-tags filter, dedup.

conftest.py handles the sys.modules stubs for MCP SDK + Supabase before
this file loads, so `from server import` works without the real deps.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import (
    _apply_temporal_scoring,
    _cosine_sim,
    _filter_excluded_tags,
    _format_memories,
    _parse_pgvector,
    _rrf_merge,
    CONFIDENCE_FLOOR,
    MAX_AUTO_LINKS,
    SUPERSEDE_SIM_THRESHOLD,
    TEMPORAL_HALF_LIVES,
)


# ---------------------------------------------------------------------------
# _rrf_merge
# ---------------------------------------------------------------------------


class TestRRFMerge:
    """Reciprocal Rank Fusion merges two ranked lists."""

    def test_empty_inputs(self):
        result = _rrf_merge([], [], limit=5)
        assert result == []

    def test_single_list(self):
        rows = [{"id": "a", "name": "mem1"}, {"id": "b", "name": "mem2"}]
        result = _rrf_merge(rows, [], limit=5)
        assert len(result) == 2
        assert result[0]["id"] == "a"
        assert result[1]["id"] == "b"

    def test_overlap_boosts_score(self):
        sem = [{"id": "a", "name": "shared"}, {"id": "b", "name": "sem-only"}]
        kw = [{"id": "a", "name": "shared"}, {"id": "c", "name": "kw-only"}]
        result = _rrf_merge(sem, kw, limit=5)
        assert result[0]["id"] == "a"
        assert result[0]["_final_score"] > result[1]["_final_score"]

    def test_limit_respected(self):
        rows = [{"id": str(i), "name": f"m{i}"} for i in range(10)]
        result = _rrf_merge(rows, [], limit=3)
        assert len(result) == 3

    def test_score_calculation(self):
        k = 60
        rows = [{"id": "a", "name": "x"}]
        result = _rrf_merge(rows, [], limit=5, k=k)
        expected = 1.0 / (k + 0)
        assert abs(result[0]["_final_score"] - expected) < 1e-10

    def test_score_both_lists(self):
        k = 60
        sem = [{"id": "a", "name": "x"}]
        kw = [{"id": "z", "name": "y"}, {"id": "a", "name": "x"}]
        result = _rrf_merge(sem, kw, limit=5, k=k)
        a_score = next(r for r in result if r["id"] == "a")
        expected = 1.0 / (k + 0) + 1.0 / (k + 1)
        assert abs(a_score["_rrf_score"] - expected) < 1e-10

    def test_name_fallback_for_id(self):
        rows = [{"name": "alpha"}, {"name": "beta"}]
        result = _rrf_merge(rows, [], limit=5)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _apply_temporal_scoring
# ---------------------------------------------------------------------------


class TestTemporalScoring:
    """Temporal scoring re-ranks by recency x access boost."""

    @staticmethod
    def _make_row(mem_type="decision", days_ago=0, accessed_days_ago=None, rrf=0.5):
        now = datetime.now(timezone.utc)
        updated = (now - timedelta(days=days_ago)).isoformat()
        accessed = (
            (now - timedelta(days=accessed_days_ago)).isoformat()
            if accessed_days_ago is not None
            else None
        )
        return {
            "type": mem_type,
            "updated_at": updated,
            "last_accessed_at": accessed,
            "_rrf_score": rrf,
        }

    def test_recent_ranks_higher(self):
        old = self._make_row(days_ago=30)
        new = self._make_row(days_ago=1)
        result = _apply_temporal_scoring([old, new])
        assert result[0]["_temporal_score"] > result[1]["_temporal_score"]

    def test_same_age_equal_scores(self):
        a = self._make_row(days_ago=5, rrf=0.5)
        b = self._make_row(days_ago=5, rrf=0.5)
        result = _apply_temporal_scoring([a, b])
        assert abs(result[0]["_temporal_score"] - result[1]["_temporal_score"]) < 1e-6

    def test_access_boost(self):
        accessed = self._make_row(days_ago=10, accessed_days_ago=1)
        not_accessed = self._make_row(days_ago=10)
        result = _apply_temporal_scoring([not_accessed, accessed])
        assert result[0]["_temporal_score"] > result[1]["_temporal_score"]

    def test_type_half_lives(self):
        project = self._make_row(mem_type="project", days_ago=14)
        feedback = self._make_row(mem_type="feedback", days_ago=14)
        result = _apply_temporal_scoring([project, feedback])
        assert result[0]["type"] == "feedback"

    def test_rrf_weight_preserved(self):
        high_rrf = self._make_row(rrf=0.9, days_ago=5)
        low_rrf = self._make_row(rrf=0.1, days_ago=5)
        result = _apply_temporal_scoring([low_rrf, high_rrf])
        assert result[0]["_temporal_score"] > result[1]["_temporal_score"]

    def test_handles_missing_timestamps(self):
        row = {"type": "decision", "updated_at": "", "last_accessed_at": None, "_rrf_score": 0.5}
        result = _apply_temporal_scoring([row])
        assert "_temporal_score" in result[0]
        assert result[0]["_temporal_score"] > 0

    def test_zero_days_ago(self):
        row = self._make_row(days_ago=0, rrf=1.0)
        result = _apply_temporal_scoring([row])
        assert result[0]["_temporal_score"] >= 0.9

    def test_sorted_descending(self):
        rows = [
            self._make_row(days_ago=30, rrf=0.3),
            self._make_row(days_ago=1, rrf=0.8),
            self._make_row(days_ago=10, rrf=0.5),
        ]
        result = _apply_temporal_scoring(rows)
        scores = [r["_temporal_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    # -- #240: confidence entrenchment multiplier ---------------------------

    def test_confidence_high_outranks_low(self):
        high = self._make_row(days_ago=5, rrf=0.5)
        high["id"] = "high"
        high["confidence"] = 1.0
        low = self._make_row(days_ago=5, rrf=0.5)
        low["id"] = "low"
        low["confidence"] = 0.5
        result = _apply_temporal_scoring([low, high])
        assert result[0]["id"] == "high"
        ratio = result[0]["_temporal_score"] / result[1]["_temporal_score"]
        assert abs(ratio - (1.0 / 0.75)) < 1e-9

    def test_confidence_null_treated_as_1(self):
        null_row = self._make_row(days_ago=5, rrf=0.5)
        null_row["id"] = "null"
        full_row = self._make_row(days_ago=5, rrf=0.5)
        full_row["id"] = "full"
        full_row["confidence"] = 1.0
        result = _apply_temporal_scoring([null_row, full_row])
        s_null = next(r["_temporal_score"] for r in result if r["id"] == "null")
        s_full = next(r["_temporal_score"] for r in result if r["id"] == "full")
        assert abs(s_null - s_full) < 1e-9

    def test_confidence_zero_gets_floor(self):
        zero = self._make_row(days_ago=5, rrf=0.5)
        zero["id"] = "zero"
        zero["confidence"] = 0.0
        full = self._make_row(days_ago=5, rrf=0.5)
        full["id"] = "full"
        full["confidence"] = 1.0
        result = _apply_temporal_scoring([zero, full])
        s_zero = next(r["_temporal_score"] for r in result if r["id"] == "zero")
        s_full = next(r["_temporal_score"] for r in result if r["id"] == "full")
        assert abs(s_zero / s_full - CONFIDENCE_FLOOR) < 1e-9


# ---------------------------------------------------------------------------
# _format_memories
# ---------------------------------------------------------------------------


class TestFormatMemories:
    """Memory formatting for LLM output."""

    def test_basic_format(self):
        mem = {
            "name": "test_mem",
            "type": "decision",
            "project": "jarvis",
            "description": "A test memory",
            "content": "Some content here",
            "tags": ["tag1", "tag2"],
            "updated_at": "2026-04-09T12:00:00+00:00",
        }
        result = _format_memories([mem])
        assert len(result) == 1
        assert "## test_mem (decision, jarvis)" in result[0]
        assert "[tag1, tag2]" in result[0]
        assert "*A test memory*" in result[0]
        assert "Some content here" in result[0]

    def test_global_project(self):
        mem = {
            "name": "x",
            "type": "user",
            "project": None,
            "description": "",
            "content": "c",
            "tags": [],
        }
        result = _format_memories([mem])
        assert "(user, global)" in result[0]

    def test_no_tags(self):
        mem = {"name": "x", "type": "user", "project": None, "description": "", "content": "c"}
        result = _format_memories([mem])
        header_line = result[0].split("\n")[0]
        assert "] " not in header_line or header_line.endswith(")")

    def test_empty_tags_list(self):
        mem = {
            "name": "x",
            "type": "user",
            "project": None,
            "description": "",
            "content": "c",
            "tags": [],
        }
        result = _format_memories([mem])
        header_line = result[0].split("\n")[0]
        assert "[]" not in header_line

    def test_link_info(self):
        mem = {
            "name": "linked_mem",
            "type": "decision",
            "project": "jarvis",
            "description": "desc",
            "content": "body",
            "tags": [],
            "link_type": "related",
            "link_strength": 0.75,
            "updated_at": "2026-04-09",
        }
        result = _format_memories([mem], link_info=True)
        assert "← related (0.75)" in result[0]

    def test_link_info_not_shown_without_flag(self):
        mem = {
            "name": "x",
            "type": "decision",
            "project": None,
            "description": "",
            "content": "c",
            "tags": [],
            "link_type": "related",
            "link_strength": 0.75,
        }
        result = _format_memories([mem], link_info=False)
        assert "← related" not in result[0]

    def test_multiple_memories(self):
        mems = [
            {
                "name": f"m{i}",
                "type": "project",
                "project": "j",
                "description": "",
                "content": f"c{i}",
                "tags": [],
            }
            for i in range(5)
        ]
        result = _format_memories(mems)
        assert len(result) == 5

    # Brief mode

    def test_brief_basic_layout(self):
        mem = {
            "name": "foo",
            "type": "feedback",
            "project": "jarvis",
            "tags": ["a", "b"],
            "description": "hello world",
            "similarity": 0.42,
            "content": "MUST_NOT_APPEAR",
        }
        result = _format_memories([mem], brief=True)
        assert result == ["- foo [feedback/jarvis] [a, b] (sim 0.42): hello world — id=?"]
        assert "MUST_NOT_APPEAR" not in result[0]

    def test_brief_global_scope(self):
        mem = {"name": "g", "type": "user", "project": None, "description": "d"}
        result = _format_memories([mem], brief=True)
        assert result[0] == "- g [user/global]: d — id=?"

    def test_brief_temporal_score_leads_when_present(self):
        mem = {
            "name": "t",
            "type": "decision",
            "project": "jarvis",
            "description": "x",
            "_temporal_score": 0.0456,
            "_rrf_score": 0.0333,
        }
        result = _format_memories([mem], brief=True)
        assert "(score 0.046; rrf 0.033)" in result[0]

    def test_brief_rrf_wins_over_similarity(self):
        mem = {
            "name": "r",
            "type": "decision",
            "description": "x",
            "_rrf_score": 0.05,
            "similarity": 0.9,
        }
        result = _format_memories([mem], brief=True)
        assert "rrf 0.050" in result[0]
        assert "sim" not in result[0]

    def test_brief_link_info(self):
        mem = {
            "name": "l",
            "type": "decision",
            "project": "jarvis",
            "description": "d",
            "similarity": 0.5,
            "link_type": "related",
            "link_strength": 0.75,
        }
        result = _format_memories([mem], link_info=True, brief=True)
        assert "← related (0.75)" in result[0]
        assert result[0].startswith("- l [decision/jarvis]")

    def test_brief_no_score_fields(self):
        mem = {"name": "n", "type": "reference", "project": None, "description": "d"}
        result = _format_memories([mem], brief=True)
        assert result[0] == "- n [reference/global]: d — id=?"

    def test_brief_empty_description(self):
        mem = {"name": "bare", "type": "decision", "project": "jarvis"}
        result = _format_memories([mem], brief=True)
        assert result[0] == "- bare [decision/jarvis]:  — id=?"


# ---------------------------------------------------------------------------
# _cosine_sim
# ---------------------------------------------------------------------------


class TestCosineSim:
    """Unit tests for cosine similarity function."""

    def test_empty_vectors(self):
        assert _cosine_sim([], []) == 0.0

    def test_none_vectors(self):
        assert _cosine_sim(None, [1.0, 0.0]) == 0.0
        assert _cosine_sim([1.0, 0.0], None) == 0.0
        assert _cosine_sim(None, None) == 0.0

    def test_identical_unit_vectors(self):
        v = [1.0, 0.0]
        assert _cosine_sim(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_sim([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_scaled_same_direction(self):
        assert _cosine_sim([1.0, 1.0], [2.0, 2.0]) == pytest.approx(1.0)

    def test_zero_magnitude_vector(self):
        assert _cosine_sim([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_length_mismatch_returns_zero(self):
        assert _cosine_sim([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0
        assert _cosine_sim([1.0] * 512, [1.0] * 1024) == 0.0


# ---------------------------------------------------------------------------
# _parse_pgvector
# ---------------------------------------------------------------------------


class TestParsePgvector:
    """supabase-py returns pgvector columns as JSON strings, not lists.

    Regression guard for the known-unknowns dedup/resolution bug where raw
    string embeddings hit the `_cosine_sim` length-mismatch guard and
    silently scored 0.
    """

    def test_list_passes_through(self):
        v = [0.1, 0.2, 0.3]
        assert _parse_pgvector(v) is v

    def test_none_returns_none(self):
        assert _parse_pgvector(None) is None

    def test_json_string_parses_to_list(self):
        parsed = _parse_pgvector("[0.1, 0.2, 0.3]")
        assert parsed == [0.1, 0.2, 0.3]

    def test_malformed_string_returns_none(self):
        assert _parse_pgvector("not-json") is None
        assert _parse_pgvector("[0.1, 0.2,") is None

    def test_non_list_json_returns_none(self):
        assert _parse_pgvector("42") is None
        assert _parse_pgvector('{"x": 1}') is None

    def test_unsupported_type_returns_none(self):
        assert _parse_pgvector(42) is None  # type: ignore[arg-type]

    def test_string_embedding_yields_nonzero_similarity(self):
        vec = [0.1] * 512
        stored_as_string = json.dumps(vec)
        parsed = _parse_pgvector(stored_as_string)
        assert parsed is not None
        assert len(parsed) == 512
        assert _cosine_sim(vec, parsed) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# #417: session-snapshot tag filter
# ---------------------------------------------------------------------------


class TestExcludedTagsFilter:
    """Osasuwu/jarvis#417: session_snapshot_* memories should not appear in recall."""

    def test_filter_drops_session_snapshot(self):
        rows = [
            {"name": "real_content", "tags": ["pillar-4"]},
            {"name": "session_snapshot_abc", "tags": ["session-snapshot", "auto"]},
            {"name": "real_decision", "tags": ["decision", "memory"]},
        ]
        out = _filter_excluded_tags(rows)
        assert [r["name"] for r in out] == ["real_content", "real_decision"]

    def test_filter_preserves_input_order(self):
        rows = [
            {"name": "a", "tags": []},
            {"name": "snap", "tags": ["session-snapshot"]},
            {"name": "b", "tags": ["x"]},
            {"name": "c", "tags": None},
        ]
        out = _filter_excluded_tags(rows)
        assert [r["name"] for r in out] == ["a", "b", "c"]

    def test_filter_handles_empty_and_missing_tags(self):
        rows = [
            {"name": "no_key"},
            {"name": "none_tags", "tags": None},
            {"name": "empty_tags", "tags": []},
        ]
        out = _filter_excluded_tags(rows)
        assert [r["name"] for r in out] == ["no_key", "none_tags", "empty_tags"]

    def test_filter_no_op_on_empty_input(self):
        assert _filter_excluded_tags([]) == []


# ---------------------------------------------------------------------------
# Recall pipeline dedup regression (bidirectional link bug)
# ---------------------------------------------------------------------------


class TestRecallLinkedDedup:
    """Regression: bidirectional links should not produce duplicate linked memories."""

    def test_dedup_within_linked_results(self):
        main_ids = {"main-1", "main-2"}
        linked = [
            {"id": "linked-A", "name": "mem_a", "link_type": "related", "link_strength": 0.71},
            {"id": "linked-A", "name": "mem_a", "link_type": "related", "link_strength": 0.63},
            {"id": "linked-B", "name": "mem_b", "link_type": "related", "link_strength": 0.65},
        ]

        found_ids = set(main_ids)
        seen_linked: set[str] = set()
        unique_linked = []
        for r in linked:
            rid = r.get("id")
            if rid not in found_ids and rid not in seen_linked:
                seen_linked.add(rid)
                unique_linked.append(r)

        assert len(unique_linked) == 2
        assert unique_linked[0]["id"] == "linked-A"
        assert unique_linked[0]["link_strength"] == 0.71
        assert unique_linked[1]["id"] == "linked-B"

    def test_main_results_excluded_from_linked(self):
        main_ids = {"id-1"}
        linked = [
            {"id": "id-1", "name": "same_as_main", "link_type": "related", "link_strength": 0.80},
            {"id": "id-2", "name": "different", "link_type": "related", "link_strength": 0.70},
        ]

        found_ids = set(main_ids)
        seen_linked: set[str] = set()
        unique_linked = []
        for r in linked:
            rid = r.get("id")
            if rid not in found_ids and rid not in seen_linked:
                seen_linked.add(rid)
                unique_linked.append(r)

        assert len(unique_linked) == 1
        assert unique_linked[0]["id"] == "id-2"
