"""Unit tests for scripts/dreamer-run.py — S4 Dreamer consolidation pipeline.

Covers:
  - ``consolidate()`` pure function with controlled LLM responses
  - ``_parse_response()`` JSON extraction and validation
  - Trigger logic (pending-count / days-since thresholds)
  - ``fetch_corpus()`` filtering and cap

Network + DB are stubbed via ``unittest.mock``. The ``consolidate()`` tests
mock ``httpx.Client`` to control LLM output. Trigger and corpus tests operate
on the pure logic / stubbed DB patterns so they don't need a real Supabase.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Stub httpx / supabase / dotenv if not installed so the module import works
# in minimal CI (same pattern as test_consolidation_review.py).
for name in ("httpx", "supabase", "dotenv"):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        if name == "httpx":
            mod.HTTPError = type("HTTPError", (Exception,), {})  # type: ignore[attr-defined]

            class _StubClient:
                def __init__(self, *a, **k):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            mod.Client = _StubClient  # type: ignore[attr-defined]
        if name == "supabase":
            mod.create_client = lambda *a, **k: None  # type: ignore[attr-defined]
        if name == "dotenv":
            mod.load_dotenv = lambda *a, **k: None  # type: ignore[attr-defined]
        sys.modules[name] = mod

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "dreamer-run.py"

spec = importlib.util.spec_from_file_location("dreamer_run", SCRIPT_PATH)
assert spec and spec.loader
dreamer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dreamer)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_corpus_memory(
    *,
    idx: int = 1,
    name: str = "",
    content: str = "Test feedback content",
    project: str | None = "jarvis",
    tags: list[str] | None = None,
) -> dict:
    uid = str(uuid.uuid4())
    return {
        "id": uid,
        "name": name or f"test_feedback_{idx}",
        "type": "feedback",
        "project": project,
        "description": f"Test feedback #{idx}",
        "content": content,
        "tags": tags or ["feedback", "test"],
        "requires_review": False,
        "created_at": f"2026-05-{10 + idx:02d}T00:00:00Z",
        "updated_at": f"2026-05-{10 + idx:02d}T00:00:00Z",
    }


def _make_llm_response(
    new_candidates: list[dict] | None = None,
    merge_proposals: list[dict] | None = None,
) -> MagicMock:
    """Return a mocked httpx response with controlled JSON body.

    The returned mock has ``.json()`` returning the Anthropic Messages
    payload shape containing the given candidates/proposals as text content.
    """
    payload = {
        "new_candidates": new_candidates or [],
        "merge_proposals": merge_proposals or [],
    }
    resp = MagicMock()
    resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }
    return resp


def _make_mock_http(resp: MagicMock) -> MagicMock:
    """Return a mock ``httpx.Client`` whose ``.post`` returns ``resp``."""
    http = MagicMock()
    http.post.return_value = resp
    return http


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_empty_text_returns_none(self):
        assert dreamer._parse_response("", set()) is None
        assert dreamer._parse_response("   ", set()) is None

    def test_no_json_returns_none(self):
        assert dreamer._parse_response("just prose", set()) is None

    def test_valid_empty_output(self):
        text = '{"new_candidates": [], "merge_proposals": []}'
        result = dreamer._parse_response(text, set())
        assert result is not None
        candidates, proposals = result
        assert candidates == []
        assert proposals == []

    def test_new_candidate_with_valid_type(self):
        text = json.dumps(
            {
                "new_candidates": [
                    {
                        "name": "test_insight",
                        "type": "project",
                        "project": "jarvis",
                        "description": "A test insight",
                        "content": "Test content here",
                        "tags": ["dreamer", "test"],
                        "reasoning": "This is a test",
                    }
                ],
                "merge_proposals": [],
            }
        )
        result = dreamer._parse_response(text, set())
        assert result is not None
        candidates, proposals = result
        assert len(candidates) == 1
        assert candidates[0]["name"] == "test_insight"
        assert candidates[0]["type"] == "project"
        assert candidates[0]["project"] == "jarvis"

    def test_new_candidate_invalid_type_is_dropped(self):
        text = json.dumps(
            {
                "new_candidates": [
                    {
                        "name": "bad_type",
                        "type": "invalid_type",
                        "description": "bad",
                        "content": "bad",
                    }
                ],
                "merge_proposals": [],
            }
        )
        result = dreamer._parse_response(text, set())
        assert result is not None
        candidates, _ = result
        assert len(candidates) == 0

    def test_merge_proposal_requires_two_or_more_targets(self):
        corpus_ids = {"id-1", "id-2", "id-3"}
        text = json.dumps(
            {
                "new_candidates": [],
                "merge_proposals": [
                    {
                        "name": "valid_merge",
                        "type": "feedback",
                        "description": "Merged feedback",
                        "content": "Merged content",
                        "tags": ["dreamer"],
                        "merge_targets": ["id-1", "id-2"],
                        "reasoning": "These overlap",
                    },
                    {
                        "name": "single_target",
                        "type": "feedback",
                        "description": "Only one target",
                        "content": "Content",
                        "tags": ["dreamer"],
                        "merge_targets": ["id-1"],
                        "reasoning": "Only one",
                    },
                    {
                        "name": "nonexistent_target",
                        "type": "feedback",
                        "description": "Target not in corpus",
                        "content": "Content",
                        "tags": ["dreamer"],
                        "merge_targets": ["id-1", "no-such-id"],
                        "reasoning": "Bad target",
                    },
                ],
            }
        )
        result = dreamer._parse_response(text, corpus_ids)
        assert result is not None
        _, proposals = result
        # Only the first proposal has both targets in corpus_ids
        assert len(proposals) == 1
        assert proposals[0]["name"] == "valid_merge"

    def test_merge_proposal_targets_filtered_to_corpus_ids(self):
        corpus_ids = {"real-uuid-1", "real-uuid-2"}
        text = json.dumps(
            {
                "new_candidates": [],
                "merge_proposals": [
                    {
                        "name": "partial_filter",
                        "type": "feedback",
                        "description": "One real, one fake",
                        "content": "Content",
                        "tags": ["dreamer"],
                        "merge_targets": ["real-uuid-1", "fake-uuid", "real-uuid-2"],
                        "reasoning": "Mixed targets",
                    },
                ],
            }
        )
        result = dreamer._parse_response(text, corpus_ids)
        assert result is not None
        _, proposals = result
        # fake-uuid is filtered out, but real-uuid-1 and real-uuid-2 remain
        assert len(proposals) == 1
        assert set(proposals[0]["merge_targets"]) == {"real-uuid-1", "real-uuid-2"}

    def test_new_candidate_strips_merge_targets(self):
        """New candidates should not carry merge_targets even if LLM emits them."""
        text = json.dumps(
            {
                "new_candidates": [
                    {
                        "name": "should_not_have_targets",
                        "type": "project",
                        "description": "A candidate",
                        "content": "Content",
                        "merge_targets": ["some-uuid"],
                    }
                ],
                "merge_proposals": [],
            }
        )
        result = dreamer._parse_response(text, {"some-uuid"})
        assert result is not None
        candidates, _ = result
        assert len(candidates) == 1
        assert "merge_targets" not in candidates[0]

    def test_duplicate_names_deduplicated(self):
        text = json.dumps(
            {
                "new_candidates": [
                    {
                        "name": "duplicate_name",
                        "type": "project",
                        "description": "First",
                        "content": "First content",
                    },
                    {
                        "name": "duplicate_name",
                        "type": "project",
                        "description": "Second (duplicate)",
                        "content": "Second content",
                    },
                ],
                "merge_proposals": [],
            }
        )
        result = dreamer._parse_response(text, set())
        assert result is not None
        candidates, _ = result
        assert len(candidates) == 1
        assert candidates[0]["description"] == "First"

    def test_prose_prefix_and_suffix(self):
        """LLM sometimes adds prose around the JSON. Parser must ignore it."""
        text = (
            "Here is my analysis:\n\n"
            '{\n  "new_candidates": [],\n  "merge_proposals": []\n}\n\n'
            "Hope this helps!"
        )
        result = dreamer._parse_response(text, set())
        assert result is not None
        candidates, proposals = result
        assert candidates == []
        assert proposals == []


# ---------------------------------------------------------------------------
# consolidate() — pure function with mocked httpx
# ---------------------------------------------------------------------------


class TestConsolidate:
    """Tests the core ``consolidate(corpus)`` pure function.

    httpx.Client is mocked to return controlled LLM responses.
    """

    def test_sparse_corpus_returns_empty(self):
        """AC: given a sparse corpus, consolidate runs without error
        and returns empty lists."""
        corpus = []
        candidates, proposals = dreamer.consolidate(corpus, api_key="test-key")
        assert candidates == []
        assert proposals == []

    def test_no_api_key_returns_empty(self):
        corpus = [_make_corpus_memory(idx=1)]
        candidates, proposals = dreamer.consolidate(corpus, api_key="")
        assert candidates == []
        assert proposals == []

    def test_llm_http_error_returns_empty(self):
        corpus = [_make_corpus_memory(idx=1)]
        http = MagicMock()
        http.post.side_effect = dreamer.httpx.HTTPError("connection failed")

        with patch.object(dreamer.httpx, "Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = http
            candidates, proposals = dreamer.consolidate(corpus, api_key="test-key")

        assert candidates == []
        assert proposals == []

    def test_near_duplicate_corpus_returns_merge_proposal(self):
        """AC: given a corpus with two near-duplicate accepted feedback
        memories, consolidate returns at least one merge proposal whose
        merge_targets contains both UUIDs."""
        mem1 = _make_corpus_memory(idx=1, content="User prefers async workflows for code review")
        mem2 = _make_corpus_memory(
            idx=2, content="User strongly prefers async code review workflows"
        )
        corpus = [mem1, mem2]

        merge_proposals = [
            {
                "name": "async_review_preference",
                "type": "user",
                "project": None,
                "description": "User prefers async code review workflows",
                "content": "User consistently prefers async workflows for code review, "
                "as seen in multiple feedback entries.",
                "tags": ["dreamer", "workflow", "code-review"],
                "merge_targets": [mem1["id"], mem2["id"]],
                "reasoning": "Both entries describe the same user preference for async code review",
            }
        ]
        llm_resp = _make_llm_response(merge_proposals=merge_proposals)
        http = _make_mock_http(llm_resp)

        with patch.object(dreamer.httpx, "Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = http
            candidates, proposals = dreamer.consolidate(corpus, api_key="test-key")

        assert len(proposals) >= 1
        found = any(
            mem1["id"] in p["merge_targets"] and mem2["id"] in p["merge_targets"] for p in proposals
        )
        assert found, (
            f"Expected a merge proposal referencing both {mem1['id']} "
            f"and {mem2['id']}, got: {proposals}"
        )

    def test_unparseable_llm_response_returns_empty(self):
        corpus = [_make_corpus_memory(idx=1)]
        resp = MagicMock()
        resp.json.return_value = {
            "content": [{"type": "text", "text": "not json at all"}],
        }
        http = _make_mock_http(resp)

        with patch.object(dreamer.httpx, "Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = http
            candidates, proposals = dreamer.consolidate(corpus, api_key="test-key")

        assert candidates == []
        assert proposals == []

    def test_response_capped_at_max(self):
        """Enforce per-category caps (MAX_NEW_CANDIDATES, MAX_MERGE_PROPOSALS)."""
        corpus = [_make_corpus_memory(idx=i) for i in range(10)]
        many_candidates = [
            {
                "name": f"insight_{i}",
                "type": "project",
                "project": "jarvis",
                "description": f"Insight #{i}",
                "content": f"Content {i}",
                "tags": ["dreamer"],
                "reasoning": "Test",
            }
            for i in range(20)
        ]
        llm_resp = _make_llm_response(new_candidates=many_candidates)
        http = _make_mock_http(llm_resp)

        with patch.object(dreamer.httpx, "Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value = http
            candidates, proposals = dreamer.consolidate(corpus, api_key="test-key")

        assert len(candidates) <= dreamer.MAX_NEW_CANDIDATES


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------


class TestTrigger:
    """Tests the trigger-check logic with a mocked client."""

    @staticmethod
    def _mock_client_with_pending(count: int) -> MagicMock:
        """Build a client mock returning ``count`` pending candidates.

        Builds a proper chain so that::
            client.table("memories").select().eq().eq().is_().limit().execute()
        returns a resp with ``.count = count``.
        The double eq() reflects fetch_pending_count's two filters:
        eq("requires_review", True) then eq("type", "feedback").
        """
        client = MagicMock()
        exec_resp = MagicMock()
        exec_resp.count = count

        limit_result = MagicMock()
        limit_result.execute.return_value = exec_resp

        is_mock = MagicMock()
        is_mock.is_.return_value.limit.return_value = limit_result

        type_eq_mock = MagicMock()
        type_eq_mock.eq.return_value = is_mock  # second eq("type", "feedback") → is_mock

        eq_mock = MagicMock()
        eq_mock.eq.return_value = type_eq_mock  # first eq("requires_review", True) → type_eq_mock

        select_mock = MagicMock()
        select_mock.select.return_value = eq_mock

        client.table.return_value = select_mock
        return client

    @staticmethod
    def _mock_client_with_events(client: MagicMock, last_event_dt: str | None) -> MagicMock:
        """Add events-table mocks to an existing client stub.

        Builds a chain::
            client.table("events").select().eq().order().limit().execute()
        returns a resp with ``.data`` containing (or not) the last event.
        """
        rows = [{"created_at": last_event_dt}] if last_event_dt else []

        exec_resp = MagicMock()
        exec_resp.data = rows

        limit_mock = MagicMock()
        limit_mock.limit.return_value.execute.return_value = exec_resp

        order_mock = MagicMock()
        order_mock.order.return_value = limit_mock

        eq_mock = MagicMock()
        eq_mock.eq.return_value = order_mock

        select_mock = MagicMock()
        select_mock.select.return_value = eq_mock

        def _table_side_effect(name: str):
            if name == "memories":
                return client.table.return_value
            elif name == "events":
                return select_mock
            return MagicMock()

        client.table.side_effect = _table_side_effect
        return client

    def test_pending_above_threshold_fires(self):
        client = self._mock_client_with_pending(dreamer.PENDING_THRESHOLD + 5)
        should_run, reason = dreamer.check_trigger(client)
        assert should_run
        assert "pending_candidate_count" in reason

    def test_pending_below_threshold_and_recent_run_skips(self):
        client = self._mock_client_with_pending(5)
        recent = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        client = self._mock_client_with_events(client, recent)
        should_run, reason = dreamer.check_trigger(client)
        assert not should_run
        assert "pending_candidate_count=5" in reason

    def test_no_prior_run_fires(self):
        """If there has never been a Dreamer run, trigger fires unconditionally."""
        client = self._mock_client_with_pending(5)
        client = self._mock_client_with_events(client, None)
        should_run, reason = dreamer.check_trigger(client)
        assert should_run
        assert "no prior" in reason

    def test_days_since_above_threshold_fires(self):
        client = self._mock_client_with_pending(5)
        client = self._mock_client_with_events(client, "2026-05-10T12:00:00Z")
        should_run, reason = dreamer.check_trigger(client)
        # Should fire because 10 >= 7 days
        assert should_run
        assert "days_since_last_run" in reason


# ---------------------------------------------------------------------------
# Corpus fetching (pure logic test via fetch_corpus client stub)
# ---------------------------------------------------------------------------


class TestFetchCorpus:
    def test_respects_max_rows(self):
        """Corpus fetch passes MAX_CORPUS_ROWS to .limit()."""
        client = MagicMock()
        expected_rows = [_make_corpus_memory(idx=i) for i in range(dreamer.MAX_CORPUS_ROWS)]
        execute_resp = MagicMock()
        execute_resp.data = expected_rows

        table_mock = MagicMock()
        select_mock = MagicMock()
        eq_mock = MagicMock()
        gte_mock = MagicMock()
        is__mock = MagicMock()
        order_mock = MagicMock()
        limit_result = MagicMock()
        limit_result.execute.return_value = execute_resp

        client.table.return_value = table_mock
        table_mock.select.return_value = select_mock
        select_mock.eq.return_value = eq_mock
        eq_mock.gte.return_value = gte_mock
        gte_mock.is_.return_value = is__mock
        is__mock.order.return_value = order_mock
        order_mock.limit.return_value = limit_result

        corpus = dreamer.fetch_corpus(client)

        order_mock.limit.assert_called_once_with(dreamer.MAX_CORPUS_ROWS)
        assert corpus == expected_rows

    def test_filters_to_feedback_type(self):
        """Only feedback-type memories are returned."""
        client = MagicMock()
        execute_resp = MagicMock()
        execute_resp.data = [_make_corpus_memory(idx=1)]

        # Track what eq filter was applied
        table_mock = MagicMock()
        eq_mock = MagicMock()
        gte_mock = MagicMock()
        is__mock = MagicMock()
        order_mock = MagicMock()
        limit_mock = MagicMock()
        limit_result = MagicMock()
        limit_result.execute.return_value = execute_resp

        client.table.return_value = table_mock
        table_mock.select.return_value = eq_mock
        eq_mock.eq.return_value = gte_mock
        gte_mock.gte.return_value = is__mock
        is__mock.is_.return_value = order_mock
        order_mock.order.return_value = limit_mock
        limit_mock.limit.return_value = limit_result

        corpus = dreamer.fetch_corpus(client)
        # Verify eq("type", "feedback") was called
        eq_mock.eq.assert_called_once_with("type", "feedback")
        assert corpus == execute_resp.data


# ---------------------------------------------------------------------------
# Integration-style: _row_for_memory output shape
# ---------------------------------------------------------------------------


class TestRowForMemory:
    def test_candidate_row_has_correct_shape(self):
        run_id = str(uuid.uuid4())
        item = {
            "name": "test_insight",
            "type": "project",
            "project": "jarvis",
            "description": "Test description",
            "content": "Test content",
            "tags": ["dreamer", "test"],
            "reasoning": "Test reasoning",
        }
        row = dreamer._row_for_memory(item, run_id)
        assert row["name"] == "test_insight"
        assert row["type"] == "project"
        assert row["project"] == "jarvis"
        assert row["requires_review"] is True
        assert row["source_provenance"] == f"dreamer:{run_id}"
        assert row["derivation_run_id"] == run_id
        assert "merge_targets" not in row

    def test_merge_proposal_row_has_merge_targets(self):
        run_id = str(uuid.uuid4())
        item = {
            "name": "merged_insight",
            "type": "feedback",
            "project": None,
            "description": "Merged description",
            "content": "Merged content",
            "tags": ["dreamer", "merge"],
            "merge_targets": ["uuid-1", "uuid-2"],
        }
        row = dreamer._row_for_memory(item, run_id, merge_targets=item["merge_targets"])
        assert row["requires_review"] is True
        assert row["source_provenance"] == f"dreamer:{run_id}"
        assert row["merge_targets"] == ["uuid-1", "uuid-2"]

    def test_invalid_type_falls_back_to_project(self):
        row = dreamer._row_for_memory(
            {"name": "test", "type": "nope", "description": "", "content": ""},
            run_id=str(uuid.uuid4()),
        )
        assert row["type"] == "project"
