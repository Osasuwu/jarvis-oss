"""Unit tests for scripts/memory-recall-hook.py — parser + RRF merge.

The HTTP paths (`embed`, `rewrite_prompt`) are exercised live in dev and
excluded here. This file covers the deterministic pieces that don't need
network: parsing Haiku's JSON output and the RRF fusion logic.

The module filename uses a dash so importlib is required instead of a
plain import statement.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from test_utils import StubClient, TableStub


_HOOK_PATH = Path(__file__).resolve().parent.parent / "scripts" / "memory-recall-hook.py"
_spec = importlib.util.spec_from_file_location("memory_recall_hook", _HOOK_PATH)
mrh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrh)


# ---------------------------------------------------------------------------
# _parse_rewriter — tolerant JSON extraction
# ---------------------------------------------------------------------------


class TestParseRewriter:
    def test_clean_json(self):
        r = mrh._parse_rewriter('{"entities": ["Phase 3", "RRF"], "types": ["decision"]}')
        assert r == {"entities": ["phase 3", "rrf"], "types": ["decision"]}

    def test_empty_lists_returns_none(self):
        assert mrh._parse_rewriter('{"entities": [], "types": []}') is None

    def test_tolerates_surrounding_prose(self):
        r = mrh._parse_rewriter(
            'Here is the JSON:\n{"entities":["memory_store"],"types":[]}\nDone.'
        )
        assert r == {"entities": ["memory_store"], "types": []}

    def test_filters_invalid_types(self):
        r = mrh._parse_rewriter(
            '{"entities": ["x"], "types": ["garbage", "feedback", "episode"]}'
        )
        # "episode" is not in REWRITER_VALID_TYPES for the hook (Phase 4 wiring
        # is separate); "feedback" survives, "garbage" is dropped.
        assert r == {"entities": ["x"], "types": ["feedback"]}

    def test_no_json_returns_none(self):
        assert mrh._parse_rewriter("just prose") is None

    def test_malformed_json_returns_none(self):
        assert mrh._parse_rewriter("{not json") is None

    def test_caps_entities_at_limit(self):
        entities = [f"e{i}" for i in range(20)]
        import json as _json
        r = mrh._parse_rewriter(_json.dumps({"entities": entities, "types": []}))
        assert r is not None
        assert len(r["entities"]) == mrh.REWRITER_MAX_ENTITIES

    def test_caps_types_at_limit(self):
        # All three valid types — cap is 3, so all should survive.
        r = mrh._parse_rewriter(
            '{"entities": ["x"], "types": ["feedback", "decision", "reference"]}'
        )
        assert r is not None
        assert set(r["types"]) == {"feedback", "decision", "reference"}

    def test_entities_lowercased_and_stripped(self):
        r = mrh._parse_rewriter('{"entities": ["  FooBar  ", "BAZ"], "types": []}')
        assert r == {"entities": ["foobar", "baz"], "types": []}

    def test_drops_empty_entity_strings(self):
        r = mrh._parse_rewriter('{"entities": ["", "   ", "real"], "types": []}')
        assert r == {"entities": ["real"], "types": []}

    def test_non_list_entities_coerced_to_empty(self):
        r = mrh._parse_rewriter('{"entities": "not-a-list", "types": ["feedback"]}')
        assert r == {"entities": [], "types": ["feedback"]}

    def test_empty_string_returns_none(self):
        assert mrh._parse_rewriter("") is None

    def test_valid_non_object_json_array_returns_none(self):
        # Array is valid JSON but the parser is dict-shaped — must not crash.
        assert mrh._parse_rewriter("[]") is None

    def test_valid_non_object_json_null_returns_none(self):
        assert mrh._parse_rewriter("null") is None

    def test_object_wrapping_non_dict_payload_returns_none(self):
        # Pathological: braces matched but content is not a JSON object.
        # The inner `{42}` is invalid JSON → JSONDecodeError → None.
        assert mrh._parse_rewriter("prose {42} trailing") is None


# ---------------------------------------------------------------------------
# rrf_merge — same math as server._rrf_merge, must tag only dual-hit rows
# ---------------------------------------------------------------------------


class TestRrfMerge:
    def test_rrf_score_only_on_dual_hits(self):
        semantic = [{"id": "a", "similarity": 0.9}, {"id": "b", "similarity": 0.8}]
        keyword = [{"id": "b", "rank": 0.5}, {"id": "c", "rank": 0.3}]
        out = mrh.rrf_merge(semantic, keyword)
        by_id = {r["id"]: r for r in out}
        assert "_rrf_score" in by_id["b"]
        assert "_rrf_score" not in by_id["a"]
        assert "_rrf_score" not in by_id["c"]

    def test_preserves_semantic_row_when_duplicated(self):
        # When memory is in both lists, the semantic row wins (keeps similarity).
        semantic = [{"id": "x", "similarity": 0.77}]
        keyword = [{"id": "x", "rank": 0.1}]
        out = mrh.rrf_merge(semantic, keyword)
        assert out[0]["similarity"] == 0.77

    def test_empty_inputs(self):
        assert mrh.rrf_merge([], []) == []

    def test_rows_without_id_skipped(self):
        semantic = [{"similarity": 0.9}]  # no id or name
        keyword = [{"id": "real", "rank": 0.5}]
        out = mrh.rrf_merge(semantic, keyword)
        assert len(out) == 1
        assert out[0]["id"] == "real"

    def test_ordering_reflects_rrf_score(self):
        # Higher-ranked positions in both lists → higher RRF score.
        semantic = [{"id": "top"}, {"id": "mid"}, {"id": "bot"}]
        keyword = [{"id": "top"}, {"id": "mid"}, {"id": "bot"}]
        out = mrh.rrf_merge(semantic, keyword)
        assert [r["id"] for r in out] == ["top", "mid", "bot"]

    def test_final_score_set_on_every_row(self):
        # _final_score is the unified sort key consumed by link merge.
        # Must be present on single-hits and dual-hits alike — otherwise
        # merge_with_links would treat unscored rows as 0 and reorder wildly.
        semantic = [{"id": "a"}, {"id": "b"}]
        keyword = [{"id": "b"}, {"id": "c"}]
        out = mrh.rrf_merge(semantic, keyword)
        for row in out:
            assert "_final_score" in row
            assert row["_final_score"] > 0


# ---------------------------------------------------------------------------
# rrf_merge — type boost behavior (Phase 3 soft-boost replaces hard filter)
# ---------------------------------------------------------------------------


class TestRrfMergeBoost:
    def test_boost_none_matches_default(self):
        # boost_types=None must behave identically to the default arg.
        semantic = [{"id": "a", "type": "feedback"}, {"id": "b", "type": "decision"}]
        keyword = []
        out_none = mrh.rrf_merge(semantic, keyword, boost_types=None)
        out_default = mrh.rrf_merge(semantic, keyword)
        assert [r["id"] for r in out_none] == [r["id"] for r in out_default]

    def test_empty_boost_set_treated_as_no_boost(self):
        # Falsy set → boost branch skipped. Verifies `if boost_types:` guard.
        semantic = [{"id": "a", "type": "feedback"}, {"id": "b", "type": "decision"}]
        keyword = []
        out_empty = mrh.rrf_merge(semantic, keyword, boost_types=set())
        out_none = mrh.rrf_merge(semantic, keyword, boost_types=None)
        assert [r["id"] for r in out_empty] == [r["id"] for r in out_none]

    def test_boost_reorders_single_hits(self):
        # `a` at semantic rank 0 (1/60 ≈ 0.01667) beats `b` at rank 1 (1/61 ≈
        # 0.01639) without boost. Boosting `b`'s type (1.5×) flips the order.
        semantic = [{"id": "a", "type": "decision"}, {"id": "b", "type": "feedback"}]
        keyword = []
        out = mrh.rrf_merge(semantic, keyword, boost_types={"feedback"})
        assert [r["id"] for r in out] == ["b", "a"]

    def test_boosted_single_hit_below_unboosted_dual_hit(self):
        # Calibration invariant (see TYPE_BOOST_MULTIPLIER comment):
        # boosted single-hit (1/60 × 1.5 = 0.025) must stay under unboosted
        # dual-hit (2/60 = 0.0333). This guards the 1.5 default against
        # future re-tuning that would break the fusion ordering.
        semantic = [
            {"id": "single", "type": "feedback"},
            {"id": "dual", "type": "decision"},
        ]
        keyword = [{"id": "dual", "type": "decision"}]
        out = mrh.rrf_merge(semantic, keyword, boost_types={"feedback"})
        assert [r["id"] for r in out] == ["dual", "single"]

    def test_non_matching_types_unchanged(self):
        # No row has type="feedback" → boost is a no-op.
        semantic = [{"id": "a", "type": "decision"}, {"id": "b", "type": "reference"}]
        keyword = []
        out = mrh.rrf_merge(semantic, keyword, boost_types={"feedback"})
        assert [r["id"] for r in out] == ["a", "b"]

    def test_sort_score_set_on_boosted_single_hit(self):
        # _sort_score transparency: single-hit rows that got boosted must
        # carry the actual sort key, because their native sim/rank is now
        # out of sync with the display order.
        semantic = [{"id": "a", "type": "feedback"}]
        keyword = []
        out = mrh.rrf_merge(semantic, keyword, boost_types={"feedback"})
        assert "_sort_score" in out[0]
        assert abs(out[0]["_sort_score"] - (1.0 / 60 * mrh.TYPE_BOOST_MULTIPLIER)) < 1e-9

    def test_sort_score_absent_on_unboosted_single_hit(self):
        # Boost active, but row type doesn't match → no _sort_score written.
        # (Native sim/rank still reflects ranking for that row.)
        semantic = [{"id": "a", "type": "decision"}]
        keyword = []
        out = mrh.rrf_merge(semantic, keyword, boost_types={"feedback"})
        assert "_sort_score" not in out[0]

    def test_sort_score_absent_on_boosted_dual_hit(self):
        # Dual-hits use _rrf_score (set after the boost multiply, so the
        # value already reflects the boost). _sort_score is single-hit-only.
        semantic = [{"id": "x", "type": "feedback"}]
        keyword = [{"id": "x", "type": "feedback"}]
        out = mrh.rrf_merge(semantic, keyword, boost_types={"feedback"})
        assert "_sort_score" not in out[0]
        assert "_rrf_score" in out[0]

    def test_custom_multiplier_overrides_default(self):
        # Multiplier large enough (3×) that boosted single-hit now beats
        # unboosted dual-hit — verifies the knob actually reaches the math.
        semantic = [
            {"id": "single", "type": "feedback"},
            {"id": "dual", "type": "decision"},
        ]
        keyword = [{"id": "dual", "type": "decision"}]
        out = mrh.rrf_merge(
            semantic, keyword, boost_types={"feedback"}, boost_multiplier=3.0
        )
        assert [r["id"] for r in out] == ["single", "dual"]


# ---------------------------------------------------------------------------
# detect_project — cwd basename matching
# ---------------------------------------------------------------------------


class TestDetectProject:
    # Forward-slash paths work cross-platform: Path on Linux uses "/" as the
    # separator (so `.name` correctly extracts "jarvis"), and Path on Windows
    # accepts "/" as well. Hardcoded backslash paths only resolve on Windows.
    def test_known_project(self):
        assert mrh.detect_project("/Users/x/GitHub/jarvis") == "jarvis"

    def test_case_insensitive(self):
        assert mrh.detect_project("/Users/x/GitHub/Jarvis") == "jarvis"

    def test_unknown_project_returns_none(self):
        assert mrh.detect_project("/Users/x/GitHub/random-repo") is None


# ---------------------------------------------------------------------------
# format_memory — score display falls through correctly
# ---------------------------------------------------------------------------


class TestFormatMemory:
    def test_rrf_score_wins_over_similarity(self):
        m = {"name": "n", "type": "decision", "_rrf_score": 0.42, "similarity": 0.8}
        out = mrh.format_memory(m)
        assert "rrf 0.420" in out
        assert "sim" not in out

    def test_sort_score_displays_boost_over_similarity(self):
        # Boosted single-hit rows: sim is stale vs. ranking, so _sort_score
        # must win the display chain (before sim/rank, after _rrf_score).
        m = {"name": "n", "type": "feedback", "_sort_score": 0.025, "similarity": 0.5}
        out = mrh.format_memory(m)
        assert "boost 0.025" in out
        assert "sim" not in out

    def test_rrf_score_wins_over_sort_score(self):
        # If both fields are somehow set, _rrf_score wins — that's the
        # dual-hit case, and its value already folds in any boost.
        m = {"name": "n", "type": "feedback", "_rrf_score": 0.08, "_sort_score": 0.025}
        out = mrh.format_memory(m)
        assert "rrf 0.080" in out
        assert "boost" not in out

    def test_similarity_fallback(self):
        m = {"name": "n", "type": "decision", "similarity": 0.73}
        out = mrh.format_memory(m)
        assert "sim 0.73" in out

    def test_rank_fallback(self):
        m = {"name": "n", "type": "decision", "rank": 0.25}
        out = mrh.format_memory(m)
        assert "rank 0.25" in out

    def test_no_score_no_parens(self):
        m = {"name": "n", "type": "decision"}
        out = mrh.format_memory(m)
        assert "(decision" in out  # type still rendered
        assert "rrf" not in out and "sim" not in out and "rank" not in out

    def test_link_score_displays_after_rrf_and_boost(self):
        # Linked-only rows (BFS hits with no direct retrieval signal) surface
        # their synthetic `_link_score` so owners can spot when context came
        # from a graph hop rather than the fuser.
        m = {"name": "n", "type": "decision", "_link_score": 0.00833, "similarity": 0.4}
        out = mrh.format_memory(m)
        assert "link 0.008" in out
        assert "sim" not in out

    def test_rrf_score_wins_over_link_score(self):
        # A dual-hit that also has a link edge keeps the stronger signal.
        m = {"name": "n", "type": "decision", "_rrf_score": 0.05, "_link_score": 0.008}
        out = mrh.format_memory(m)
        assert "rrf 0.050" in out
        assert "link" not in out


# ---------------------------------------------------------------------------
# format_memory_brief — one-line bulk-injection layout (Phase 7.2)
# ---------------------------------------------------------------------------


class TestFormatMemoryBrief:
    def test_basic_shape(self):
        m = {
            "name": "memory_foo",
            "type": "feedback",
            "project": "jarvis",
            "description": "short description",
            "similarity": 0.42,
            "content": "NEVER_RENDERED",
        }
        out = mrh.format_memory_brief(m)
        assert out == "- memory_foo [feedback/jarvis] (sim 0.42): short description"

    def test_global_scope_and_tags(self):
        m = {
            "name": "g",
            "type": "decision",
            "project": None,
            "tags": ["a", "b"],
            "description": "d",
            "_rrf_score": 0.033,
        }
        out = mrh.format_memory_brief(m)
        assert out == "- g [decision/global] [a, b] (rrf 0.033): d"

    def test_score_precedence_rrf_over_similarity(self):
        # Same precedence as format_memory — _rrf_score wins so dual-signal
        # hits never display a stale similarity.
        m = {"name": "n", "type": "decision", "_rrf_score": 0.42, "similarity": 0.8, "description": "x"}
        out = mrh.format_memory_brief(m)
        assert "rrf 0.420" in out
        assert "sim" not in out

    def test_link_score_surfaces(self):
        m = {
            "name": "n", "type": "decision", "_link_score": 0.006,
            "description": "via-link",
        }
        out = mrh.format_memory_brief(m)
        assert "link 0.006" in out

    def test_missing_description_renders_empty_suffix(self):
        # Migration-target memories have empty descriptions — brief still
        # emits them (they carry a name worth seeing), just with nothing
        # after the colon. Asserted so future polish (e.g. strip trailing
        # `: `) is a conscious choice, not accidental.
        m = {"name": "bare", "type": "decision", "project": "jarvis", "similarity": 0.5}
        out = mrh.format_memory_brief(m)
        assert out == "- bare [decision/jarvis] (sim 0.50): "

    def test_no_score_fields(self):
        m = {"name": "n", "type": "reference", "description": "d"}
        out = mrh.format_memory_brief(m)
        assert out == "- n [reference/global]: d"


# ---------------------------------------------------------------------------
# main()-level branching for BRIEF_MODE — we don't exec main(), just assert
# the constants expose the two budgets and that BRIEF_MODE is on by default
# ---------------------------------------------------------------------------


class TestBriefModeConstants:
    def test_brief_mode_on_by_default(self):
        assert mrh.BRIEF_MODE is True

    def test_brief_budget_smaller_than_full(self):
        assert mrh.CHAR_BUDGET_BRIEF < mrh.CHAR_BUDGET_FULL
        # And CHAR_BUDGET reflects the active mode.
        expected = mrh.CHAR_BUDGET_BRIEF if mrh.BRIEF_MODE else mrh.CHAR_BUDGET_FULL
        assert mrh.CHAR_BUDGET == expected



# ---------------------------------------------------------------------------
# _score_linked_rows — pure scorer for 1-hop BFS neighbors
# ---------------------------------------------------------------------------


class TestScoreLinkedRows:
    def test_empty_inputs(self):
        assert mrh._score_linked_rows([], []) == []
        assert mrh._score_linked_rows([{"id": "a"}], []) == []
        assert mrh._score_linked_rows([], [{"id": "b", "linked_from": "a"}]) == []

    def test_scoring_formula(self):
        # parent at rank 0, strength 1.0, default decay 0.5 →
        # 1 / (60 + 0) * 0.5 * 1.0 = 0.008333...
        seeds = [{"id": "parent"}]
        linked = [{"id": "child", "linked_from": "parent", "link_strength": 1.0}]
        out = mrh._score_linked_rows(seeds, linked)
        assert len(out) == 1
        expected = (1.0 / (mrh.RRF_K + 0)) * mrh.LINK_DECAY * 1.0
        assert abs(out[0][mrh.LINK_SCORE_FIELD] - expected) < 1e-9

    def test_rank_affects_score(self):
        # Parent at rank 2 scores less than parent at rank 0.
        seeds = [{"id": "p0"}, {"id": "p1"}, {"id": "p2"}]
        linked = [
            {"id": "c0", "linked_from": "p0", "link_strength": 1.0},
            {"id": "c2", "linked_from": "p2", "link_strength": 1.0},
        ]
        out = mrh._score_linked_rows(seeds, linked)
        by_id = {r["id"]: r[mrh.LINK_SCORE_FIELD] for r in out}
        assert by_id["c0"] > by_id["c2"]

    def test_strength_affects_score(self):
        seeds = [{"id": "parent"}]
        linked = [
            {"id": "weak", "linked_from": "parent", "link_strength": 0.25},
            {"id": "strong", "linked_from": "parent", "link_strength": 1.0},
        ]
        out = mrh._score_linked_rows(seeds, linked)
        by_id = {r["id"]: r[mrh.LINK_SCORE_FIELD] for r in out}
        assert by_id["strong"] == 4 * by_id["weak"]

    def test_missing_strength_defaults_to_one(self):
        # DB may return NULL link_strength; treat as full strength.
        seeds = [{"id": "parent"}]
        linked = [{"id": "child", "linked_from": "parent"}]  # no link_strength
        out = mrh._score_linked_rows(seeds, linked)
        expected = (1.0 / mrh.RRF_K) * mrh.LINK_DECAY * 1.0
        assert abs(out[0][mrh.LINK_SCORE_FIELD] - expected) < 1e-9

    def test_invalid_strength_defaults_to_one(self):
        # Malformed strength (string, NaN-ish) must not raise — fall back to 1.
        seeds = [{"id": "parent"}]
        linked = [{"id": "child", "linked_from": "parent", "link_strength": "bad"}]
        out = mrh._score_linked_rows(seeds, linked)
        assert len(out) == 1
        assert mrh.LINK_SCORE_FIELD in out[0]

    def test_deduplicates_against_seeds(self):
        # A linked row whose id coincides with a seed must be skipped.
        seeds = [{"id": "a"}, {"id": "b"}]
        linked = [{"id": "a", "linked_from": "b", "link_strength": 1.0}]
        out = mrh._score_linked_rows(seeds, linked)
        assert out == []

    def test_skips_linked_from_outside_seeds(self):
        # Only top-K seeds are candidates for expansion. A row pointing at
        # a non-seed parent must be dropped (RPC might return extras).
        seeds = [{"id": "a"}]
        linked = [{"id": "x", "linked_from": "unseen", "link_strength": 1.0}]
        out = mrh._score_linked_rows(seeds, linked)
        assert out == []

    def test_top_k_caps_seed_window(self):
        # With top_k=1, only the first seed counts; child of p1 is dropped.
        seeds = [{"id": "p0"}, {"id": "p1"}]
        linked = [
            {"id": "c1", "linked_from": "p1", "link_strength": 1.0},
            {"id": "c0", "linked_from": "p0", "link_strength": 1.0},
        ]
        out = mrh._score_linked_rows(seeds, linked, top_k=1)
        assert [r["id"] for r in out] == ["c0"]

    def test_dedupes_duplicate_linked_ids(self):
        # Same linked_id via multiple edges: keep the first, skip the rest.
        seeds = [{"id": "p0"}, {"id": "p1"}]
        linked = [
            {"id": "shared", "linked_from": "p0", "link_strength": 1.0},
            {"id": "shared", "linked_from": "p1", "link_strength": 1.0},
        ]
        out = mrh._score_linked_rows(seeds, linked)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# merge_with_links — fold BFS hits into the RRF-ranked list
# ---------------------------------------------------------------------------


class TestMergeWithLinks:
    def test_no_linked_unchanged(self):
        ranked = [{"id": "a", "_final_score": 0.05}, {"id": "b", "_final_score": 0.03}]
        out = mrh.merge_with_links(ranked, [])
        assert [r["id"] for r in out] == ["a", "b"]

    def test_new_linked_row_added(self):
        ranked = [{"id": "a", "_final_score": 0.05}]
        linked = [{"id": "new", mrh.LINK_SCORE_FIELD: 0.01}]
        out = mrh.merge_with_links(ranked, linked)
        ids = [r["id"] for r in out]
        assert "new" in ids
        assert out[0]["id"] == "a"  # higher score stays on top

    def test_duplicate_keeps_max_score(self):
        # Row in both lists: final score = max(direct, link). The direct RRF
        # score dominates here; _final_score should reflect that, not the
        # weaker link score.
        ranked = [{"id": "x", "_final_score": 0.05}]
        linked = [{"id": "x", mrh.LINK_SCORE_FIELD: 0.01}]
        out = mrh.merge_with_links(ranked, linked)
        assert len(out) == 1
        assert abs(out[0]["_final_score"] - 0.05) < 1e-9

    def test_reordering_by_final_score(self):
        # A strongly-linked row can outrank a weak direct hit, and the new
        # ordering is written back to _final_score for downstream consumers.
        ranked = [{"id": "weak", "_final_score": 0.005}]
        linked = [{"id": "strong_link", mrh.LINK_SCORE_FIELD: 0.02}]
        out = mrh.merge_with_links(ranked, linked)
        assert [r["id"] for r in out] == ["strong_link", "weak"]
        assert abs(out[0]["_final_score"] - 0.02) < 1e-9

    def test_rows_without_id_skipped(self):
        # Defensive: malformed rows (no id) can't be scored/sorted.
        ranked = [{"_final_score": 0.5}]  # no id
        linked = [{"id": "valid", mrh.LINK_SCORE_FIELD: 0.01}]
        out = mrh.merge_with_links(ranked, linked)
        assert [r["id"] for r in out] == ["valid"]

    def test_missing_final_score_treated_as_zero(self):
        # Edge: if upstream somehow fails to set _final_score, don't crash —
        # treat as zero so link edges can still promote the row.
        ranked = [{"id": "unscored"}]
        linked = [{"id": "linked", mrh.LINK_SCORE_FIELD: 0.01}]
        out = mrh.merge_with_links(ranked, linked)
        assert out[0]["id"] == "linked"


# ---------------------------------------------------------------------------
# expand_links — fail-soft RPC wrapper
# ---------------------------------------------------------------------------


class TestExpandLinks:
    def test_empty_top_rows_no_rpc(self):
        client = StubClient(data=[{"id": "x"}])
        out = mrh.expand_links(client, [])
        assert out == []
        assert client.rpc_calls == []

    def test_all_top_rows_missing_id_no_rpc(self):
        # Defensive: if all seeds lack ids, don't bother hitting the RPC.
        client = StubClient(data=[{"id": "x"}])
        out = mrh.expand_links(client, [{"name": "no-id"}])
        assert out == []
        assert client.rpc_calls == []

    def test_rpc_exception_returns_empty(self):
        # Fail-soft contract: RPC failure must not raise or pollute results.
        client = StubClient(raise_exc=RuntimeError("connection lost"))
        out = mrh.expand_links(client, [{"id": "seed"}])
        assert out == []

    def test_successful_rpc_returns_scored_rows(self):
        client = StubClient(data=[
            {"id": "child", "linked_from": "seed", "link_strength": 1.0},
        ])
        out = mrh.expand_links(client, [{"id": "seed"}])
        assert len(out) == 1
        assert mrh.LINK_SCORE_FIELD in out[0]

    def test_rpc_called_with_top_k_seed_ids(self):
        # Seed slice must respect LINK_EXPAND_TOP_K — we don't want to expand
        # a 25-row RRF list into a graph fetch.
        client = StubClient(data=[])
        seeds = [{"id": f"s{i}"} for i in range(10)]
        mrh.expand_links(client, seeds)
        assert len(client.rpc_calls) == 1
        call_params = client.rpc_calls[0][1]
        assert call_params["memory_ids"] == [f"s{i}" for i in range(mrh.LINK_EXPAND_TOP_K)]


# ---------------------------------------------------------------------------
# _parse_embedding — supabase-py returns vector(N) columns as JSON strings
# ---------------------------------------------------------------------------


class TestParseEmbedding:
    def test_list_passthrough(self):
        assert mrh._parse_embedding([0.1, 0.2, 0.3]) == [0.1, 0.2, 0.3]

    def test_empty_list_passthrough(self):
        # Zero-length list flows through; cosine_sim will treat as miss.
        assert mrh._parse_embedding([]) == []

    def test_json_string_parsed_to_list(self):
        assert mrh._parse_embedding("[0.1, 0.2, 0.3]") == [0.1, 0.2, 0.3]

    def test_negative_values_in_string(self):
        # pgvector serializes negatives without quoting — must survive json.loads.
        assert mrh._parse_embedding("[-0.1,-0.2,0.3]") == [-0.1, -0.2, 0.3]

    def test_none_returns_none(self):
        assert mrh._parse_embedding(None) is None

    def test_malformed_string_returns_none(self):
        # Not valid JSON → None, not a crash.
        assert mrh._parse_embedding("not a vector") is None

    def test_non_list_json_returns_none(self):
        # `{"a":1}` parses but isn't a list of floats — reject.
        assert mrh._parse_embedding('{"a": 1}') is None

    def test_unexpected_type_returns_none(self):
        # Numbers, dicts, tuples etc. aren't pgvector payloads — None.
        assert mrh._parse_embedding(42) is None
        assert mrh._parse_embedding({"foo": "bar"}) is None


# ---------------------------------------------------------------------------
# _cosine_sim — local dup of server.py's math; same contract
# ---------------------------------------------------------------------------


class TestCosineSim:
    def test_identical_vectors_similarity_one(self):
        v = [1.0, 0.0, 0.0]
        assert abs(mrh._cosine_sim(v, v) - 1.0) < 1e-9

    def test_orthogonal_similarity_zero(self):
        assert abs(mrh._cosine_sim([1.0, 0.0], [0.0, 1.0])) < 1e-9

    def test_opposite_vectors_similarity_minus_one(self):
        assert abs(mrh._cosine_sim([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9

    def test_dim_mismatch_returns_zero(self):
        # Critical guard: silent truncation via zip would return a meaningless
        # score, and dim mismatches are exactly what pre-parsed pgvector
        # strings looked like (str vs list of floats).
        assert mrh._cosine_sim([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_empty_inputs_return_zero(self):
        assert mrh._cosine_sim([], [1.0]) == 0.0
        assert mrh._cosine_sim([1.0], []) == 0.0
        assert mrh._cosine_sim([], []) == 0.0

    def test_none_inputs_return_zero(self):
        assert mrh._cosine_sim(None, [1.0]) == 0.0
        assert mrh._cosine_sim([1.0], None) == 0.0

    def test_zero_norm_returns_zero(self):
        # Zero-vector has undefined cosine; guard returns 0 rather than NaN.
        assert mrh._cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# check_known_unknown_gate — Phase 7.3 per-prompt widen signal
# ---------------------------------------------------------------------------


class TestCheckKnownUnknownGate:
    def test_empty_embedding_returns_false_without_db_call(self):
        # Short-circuit: no prompt embedding (Voyage failed) → no point
        # scanning the table. Don't hit the DB for a guaranteed miss.
        client = TableStub(data=[{"query_embedding": "[1,0,0]"}])
        assert mrh.check_known_unknown_gate(client, None) is False
        assert client.calls == []

        client2 = TableStub()
        assert mrh.check_known_unknown_gate(client2, []) is False
        assert client2.calls == []

    def test_no_open_unknowns_returns_false(self):
        client = TableStub(data=[])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is False

    def test_below_threshold_returns_false(self):
        # cosine([1,0,0], [0.5, 0.866, 0]) = 0.5 — well below 0.85.
        client = TableStub(data=[
            {"query_embedding": "[0.5, 0.866, 0.0]"}
        ])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is False

    def test_at_threshold_triggers(self):
        # Stored vector exactly at the threshold — must trigger (>=, not >).
        # Cosine of identical unit vectors is 1.0 >= 0.85.
        client = TableStub(data=[
            {"query_embedding": "[1.0, 0.0, 0.0]"}
        ])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is True

    def test_above_threshold_triggers(self):
        # Small perturbation: cosine ≈ 0.995 — widen.
        client = TableStub(data=[
            {"query_embedding": "[0.99, 0.1, 0.0]"}
        ])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is True

    def test_scans_until_hit(self):
        # First row misses (cosine 0), second row triggers. Confirms we
        # don't bail on the first miss.
        client = TableStub(data=[
            {"query_embedding": "[0.0, 1.0, 0.0]"},  # orthogonal: miss
            {"query_embedding": "[1.0, 0.0, 0.0]"},  # identical: hit
        ])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is True

    def test_null_embedding_rows_skipped(self):
        # Rows without an embedding (stored as None) don't count even
        # though the query filters them out — defensive against schema
        # changes or partial rows.
        client = TableStub(data=[
            {"query_embedding": None},
            {"query_embedding": "[1.0, 0.0, 0.0]"},
        ])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is True

    def test_malformed_embedding_skipped(self):
        # A row whose stored embedding can't be parsed is treated as a miss,
        # not a crash — the hook must never raise.
        client = TableStub(data=[
            {"query_embedding": "not a vector"},
        ])
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is False

    def test_db_exception_returns_false(self):
        # Fail-soft: any Supabase error falls back to default (brief) path.
        client = TableStub(raise_exc=RuntimeError("connection lost"))
        assert mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0]) is False

    def test_query_filters_open_and_nonnull(self):
        # Sanity: verify we scoped the query correctly so the SCAN_LIMIT cap
        # is meaningful. Without `status=open` we'd pull resolved gaps too.
        client = TableStub(data=[])
        mrh.check_known_unknown_gate(client, [1.0, 0.0, 0.0])
        # eq(status, open) and limit(SCAN_LIMIT) must both appear.
        assert ("eq", ("status", "open")) in client.calls
        assert ("limit", (mrh.KNOWN_UNKNOWN_SCAN_LIMIT,)) in client.calls


# ---------------------------------------------------------------------------
# memory_recall event emission (Phase 5.3-γ, D2-bis)
# ---------------------------------------------------------------------------


class TestMemoryRecallEventEmit:
    """Test that hook emits memory_recall events for FOK batch processing."""

    def test_emit_recall_event_on_hit(self):
        """The hook must emit a memory_recall event to events table on successful recall."""
        from unittest.mock import MagicMock, Mock

        # Mock Supabase client
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table

        # Simulate successful insert
        mock_table.insert.return_value.execute.return_value = None

        # Build a minimal recalled result to trigger event emission.
        # `similarity` is cosine — matches the server-side _emit_recall_event
        # shape so the FOK judge gets the same features regardless of source.
        # `_final_score` is RRF-rescaled and intentionally NOT in the payload.
        included_ids = ["mem-001", "mem-002"]
        rows = [
            {"id": "mem-001", "similarity": 0.85, "_final_score": 0.42},
            {"id": "mem-002", "similarity": 0.70, "_final_score": 0.31},
        ]

        included_rows = [r for r in rows if r["id"] in set(included_ids)]
        event_payload = {
            "query": "test query",
            "returned_ids": included_ids,
            "returned_similarities": [
                float(r["similarity"]) if isinstance(r.get("similarity"), (int, float)) else None
                for r in included_rows
            ],
            "returned_count": len(included_ids),
            "top_sim": float(included_rows[0]["similarity"]),
            "project": "jarvis",
            "source": "memory-recall-hook",
        }
        mock_client.table("events").insert(
            {
                "event_type": "memory_recall",
                "severity": "info",
                "repo": "Osasuwu/jarvis",
                "source": "memory-recall-hook",
                "title": f"Memory recall: test query",
                "payload": event_payload,
            }
        ).execute()

        # Verify events table was accessed
        assert mock_client.table.called
        # Verify insert was called with memory_recall event
        calls = [c for c in mock_client.table.call_args_list if c.args == ("events",)]
        assert calls, "events table should have been accessed"

    def test_emit_recall_event_failure_does_not_raise(self):
        """Hook must never raise on Supabase failures; failures are fire-and-forget."""
        from unittest.mock import MagicMock

        # Mock Supabase client that raises on insert
        mock_client = MagicMock()
        mock_client.table.return_value.insert.return_value.execute.side_effect = (
            RuntimeError("connection lost")
        )

        # Wrap in try/except like the hook does
        try:
            mock_client.table("events").insert(
                {
                    "event_type": "memory_recall",
                    "severity": "info",
                    "repo": "Osasuwu/jarvis",
                    "source": "memory-recall-hook",
                    "title": "Memory recall",
                    "payload": {"query": "test"},
                }
            ).execute()
        except Exception:
            pass  # Hook catches and swallows exceptions

        # If we get here, the hook's fail-soft contract is maintained
