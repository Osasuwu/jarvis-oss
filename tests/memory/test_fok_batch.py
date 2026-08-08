"""Tests for fok-batch.py — feeling-of-knowing batch processor (#250)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Load fok_batch module from file
spec = importlib.util.spec_from_file_location(
    "fok_batch", Path(__file__).parent.parent.parent / "scripts" / "fok-batch.py"
)
fok_batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fok_batch)


def test_build_user_message():
    """Test building the user message for Haiku with query and memory snippets."""
    query = "What's the best way to handle async errors?"
    returned_results = [
        {
            "id": "mem-001",
            "content": "Async errors should be caught with try-catch.",
            "similarity": 0.92,
        },
        {
            "id": "mem-002",
            "content": "Use Promise.catch for promise-based code.",
            "similarity": 0.85,
        },
        {
            "id": "mem-003",
            "content": "Consider error boundaries in React.",
            "similarity": 0.78,
        },
    ]

    msg = fok_batch.build_user_message(query, returned_results)

    assert "What's the best way to handle async errors?" in msg
    assert "mem-001" in msg
    assert "0.92" in msg
    assert "Async errors should be caught" in msg


def test_build_user_message_empty_results():
    """Test building message with empty results."""
    query = "Some query"
    msg = fok_batch.build_user_message(query, [])

    assert query in msg
    assert "No memories returned" in msg or "empty" in msg.lower()


def test_build_user_message_truncation():
    """Test that long memory content is truncated."""
    query = "test"
    long_content = "x" * 5000
    returned_results = [{"id": "mem-001", "content": long_content, "similarity": 0.9}]

    msg = fok_batch.build_user_message(query, returned_results)

    # Message should not contain the entire 5000-char string
    assert long_content not in msg
    assert len(msg) < len(long_content)


def test_judge_via_haiku_missing_api_key():
    """Test graceful failure when ANTHROPIC_API_KEY is missing."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
        result = fok_batch.judge_via_haiku("query", [])

        assert result["verdict"] == "unknown"
        assert result["confidence"] is None
        reason = result.get("reason", "").lower()
        assert "not set" in reason or "error" in reason or "key" in reason


def test_judge_via_haiku_json_in_prose():
    """Test that JSON parsing with regex extraction works (real API test skipped)."""
    # Test the regex extraction pattern works correctly
    json_response = {
        "verdict": "sufficient",
        "confidence": 0.85,
        "reason": "Memories directly answer the query.",
    }

    # The prose text as it would come from Haiku
    prose = f"""
    Based on the query and returned memories, here's my assessment:

    {json.dumps(json_response)}

    This is a high-confidence judgment because the query is straightforward.
    """

    # Extract JSON using the same pattern as the function
    import re

    json_match = re.search(r"\{[^{}]*\}", prose)
    assert json_match is not None
    extracted = json.loads(json_match.group())
    assert extracted["verdict"] == "sufficient"
    assert extracted["confidence"] == 0.85


def test_judge_via_haiku_missing_httpx():
    """Test graceful failure when httpx is unavailable."""
    # Temporarily mock httpx as None
    original_httpx = fok_batch.httpx
    try:
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            fok_batch.httpx = None

            result = fok_batch.judge_via_haiku("query", [])

            assert result["verdict"] == "unknown"
            reason = result.get("reason", "").lower()
            assert "httpx" in reason or "not available" in reason
    finally:
        fok_batch.httpx = original_httpx


def _mock_httpx_response(text_content: str) -> Mock:
    """Build a mock httpx response that returns the given text content."""
    mock_resp = Mock()
    mock_resp.json.return_value = {"content": [{"type": "text", "text": text_content}]}
    return mock_resp


def test_judge_via_haiku_parses_json_from_prose():
    """Response fixture: exercises the real JSON-parsing path through
    ``judge_via_haiku`` with a mocked HTTP response."""
    prose = (
        "Based on my assessment:\n"
        '{"verdict": "sufficient", "confidence": 0.85, "reason": "Memories directly answer."}\n'
        "The query is straightforward."
    )
    mock_resp = _mock_httpx_response(prose)
    with patch.object(fok_batch, "httpx") as mock_httpx:
        mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_resp
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = fok_batch.judge_via_haiku("test query", [])

    assert result["verdict"] == "sufficient"
    assert result["confidence"] == 0.85
    assert result["reason"] == "Memories directly answer."


def test_judge_via_haiku_returns_unknown_on_malformed_json():
    """Malformed JSON in Haiku response yields ``unknown`` verdict."""
    prose = (
        "Here is my judgment:\n"
        '{"verdict": "insufficient", "confidence": 0.7, "reason": "Missing contextual data"\n'  # missing trailing }
        "The memories are not sufficient."
    )
    mock_resp = _mock_httpx_response(prose)
    with patch.object(fok_batch, "httpx") as mock_httpx:
        mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_resp
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = fok_batch.judge_via_haiku("test query", [])

    assert result["verdict"] == "unknown"
    assert result["confidence"] is None


def test_judge_via_haiku_returns_unknown_on_empty_response():
    """Empty or non-JSON response yields ``unknown`` verdict."""
    prose = "I cannot assess this query without more context."
    mock_resp = _mock_httpx_response(prose)
    with patch.object(fok_batch, "httpx") as mock_httpx:
        mock_httpx.Client.return_value.__enter__.return_value.post.return_value = mock_resp
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = fok_batch.judge_via_haiku("test query", [])

    assert result["verdict"] == "unknown"
    assert result["confidence"] is None
    reason = result.get("reason", "").lower()
    assert "parse" in reason or "json" in reason


def test_try_insert_known_unknown_sufficient_verdict():
    """Test that 'sufficient' verdicts don't get inserted."""
    mock_client = MagicMock()
    event = {
        "id": "evt-001",
        "payload": {
            "query": "test",
            "top_sim": 0.95,
        },
    }
    verdict = {"verdict": "sufficient", "confidence": 0.9}

    # Should not insert for sufficient verdict
    fok_batch.try_insert_known_unknown(mock_client, event, verdict, "test-project")

    # Verify no insert was attempted
    assert not mock_client.table.called


def test_try_insert_known_unknown_high_confidence_insufficient():
    """Test that high-confidence insufficient verdicts don't get inserted."""
    mock_client = MagicMock()
    event = {
        "id": "evt-001",
        "payload": {
            "query": "test",
            "top_sim": 0.4,
        },
    }
    verdict = {"verdict": "insufficient", "confidence": 0.95}  # Too high

    fok_batch.try_insert_known_unknown(mock_client, event, verdict, "test-project")

    # High confidence insufficient should not insert
    assert not mock_client.table.called


def test_try_insert_known_unknown_low_confidence_insufficient():
    """Test that low-confidence insufficient verdicts get inserted."""
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_client.table.return_value = mock_table

    event = {
        "id": "evt-001",
        "payload": {
            "query": "what is the meaning of life",
            "top_sim": 0.35,
            "returned_ids": ["mem-001"],
        },
    }
    verdict = {"verdict": "insufficient", "confidence": 0.65}  # Low confidence

    fok_batch.try_insert_known_unknown(mock_client, event, verdict, "test-project")

    # Should attempt to insert
    mock_client.table.assert_called()


def test_try_insert_known_unknown_missing_table():
    """Test graceful handling when known_unknowns table doesn't exist."""
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_table.insert.side_effect = Exception('relation "known_unknowns" does not exist')
    mock_client.table.return_value = mock_table

    event = {
        "id": "evt-001",
        "payload": {
            "query": "test",
            "top_sim": 0.3,
        },
    }
    verdict = {"verdict": "insufficient", "confidence": 0.6}

    # Should not raise; silently catch exception
    fok_batch.try_insert_known_unknown(mock_client, event, verdict, "test-project")


def test_try_insert_known_unknown_above_similarity_threshold():
    """Test that high-similarity matches skip insertion (dedupe)."""
    mock_client = MagicMock()

    # Mock the similarity search to return high-similarity match
    mock_sim_result = Mock()
    mock_sim_result.data = [{"similarity": 0.95}]  # Above 0.7 threshold
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
        mock_sim_result
    )

    event = {
        "id": "evt-001",
        "payload": {
            "query": "duplicate query",
            "top_sim": 0.3,
        },
    }
    verdict = {"verdict": "insufficient", "confidence": 0.6}

    fok_batch.try_insert_known_unknown(mock_client, event, verdict, "test-project")

    # Should have checked similarity but not inserted (match too similar)
    mock_client.table.assert_called()


def test_write_verdict_to_event():
    """Canonical write: write_verdict_to_event writes to fok_judgments (no legacy mirror)."""
    mock_client = MagicMock()
    # Stub the events.payload fetch so write_verdict_to_event finds a row
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(
        data=[{"payload": {"query": "test", "project": "Osasuwu/jarvis"}}]
    )
    event_id = "evt-001"
    verdict = {"verdict": "partial", "confidence": 0.7, "reason": "Some info present."}

    fok_batch.write_verdict_to_event(mock_client, event_id, verdict)

    # fok_judgments.upsert was called with verdict data
    upsert_calls = [c for c in mock_client.table.return_value.upsert.call_args_list if c.args]
    assert upsert_calls, "fok_judgments.upsert should fire"
    record = upsert_calls[0].args[0]
    assert record["verdict"] == "partial"
    assert record["confidence"] == 0.7
    assert record["rationale"] == "Some info present."


def test_write_verdict_to_event_dual_writes_to_fok_judgments():
    """Canonical write: fok_judgments table receives an upsert with the right shape."""
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(
        data=[
            {
                "payload": {
                    "query": "what is fok?",
                    "project": "Osasuwu/jarvis",
                }
            }
        ]
    )
    event_id = "evt-042"
    verdict = {
        "verdict": "insufficient",
        "confidence": 0.42,
        "reason": "Memories don't address the query.",
    }

    fok_batch.write_verdict_to_event(mock_client, event_id, verdict)

    # Find the fok_judgments call specifically (events.update is also called)
    upsert_calls = [c for c in mock_client.table.return_value.upsert.call_args_list if c.args]
    assert upsert_calls, "fok_judgments.upsert should fire"

    # Confirm the table name was fok_judgments at least once
    table_names = [c.args[0] for c in mock_client.table.call_args_list if c.args]
    assert "fok_judgments" in table_names

    record = upsert_calls[0].args[0]
    assert record["recall_event_id"] == event_id
    assert record["query"] == "what is fok?"
    assert record["project"] == "Osasuwu/jarvis"
    assert record["verdict"] == "insufficient"
    assert record["confidence"] == 0.42
    assert record["rationale"] == "Memories don't address the query."
    assert record["judge_version"] == "5.3-δ"
    assert "judged_at" in record

    # on_conflict for idempotent retries
    assert upsert_calls[0].kwargs.get("on_conflict") == "recall_event_id"


def test_write_event_summary():
    """Test writing fok_run summary event."""
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_client.table.return_value = mock_table

    summary = {
        "processed": 10,
        "verdicts": {"sufficient": 6, "partial": 3, "insufficient": 1},
    }

    fok_batch.write_event(mock_client, summary, "test-project")

    # Verify insert was called
    mock_client.table.assert_called_with("events")
    mock_table.insert.assert_called_once()
    insert_call = mock_table.insert.call_args
    assert insert_call is not None
    payload = insert_call[0][0]
    assert payload.get("event_type") == "fok_run"


def test_fetch_events_filters_unfudged():
    """Test that fetch_events only returns events without fok_judgments rows (LEFT JOIN filter)."""
    mock_client = MagicMock()
    # In the new implementation, fetch_events uses LEFT JOIN to find events without
    # matching fok_judgments rows. The test mocks the filtered result.
    mock_response = Mock()
    mock_response.data = [
        {"id": "evt-001", "payload": {"query": "test"}},  # No fok_judgments row
        # evt-002 would have a fok_judgments row, so filtered out by LEFT JOIN
        {"id": "evt-003", "payload": {"query": "test"}},  # No fok_judgments row
    ]
    mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

    events = fok_batch.fetch_events(mock_client, 10)

    assert len(events) == 2
    assert events[0]["id"] == "evt-001"
    assert events[1]["id"] == "evt-003"


def test_try_insert_zero_confidence_is_not_treated_as_missing():
    """Regression guard: confidence=0.0 must still qualify for known_unknowns.

    Previous `(confidence or 1.0) >= 0.7` coerced 0.0 to 1.0 and silently
    skipped the most uncertain verdicts.
    """
    client = MagicMock()
    # known_unknowns exists probe
    client.table.return_value.select.return_value.limit.return_value.execute.return_value = Mock(
        data=[]
    )
    # dedupe: no existing row
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = Mock(
        data=[]
    )
    event = {
        "id": "evt-zero",
        "payload": {
            "top_sim": 0.1,
            "query": "zero-confidence query",
        },
    }
    verdict = {"verdict": "insufficient", "confidence": 0.0}

    fok_batch.try_insert_known_unknown(client, event, verdict, "jarvis")

    insert_calls = [c for c in client.table.return_value.insert.call_args_list if c.args]
    assert insert_calls, "insert() should have been called for confidence=0.0"
    payload = insert_calls[-1].args[0]
    assert payload["query"] == "zero-confidence query"
    assert payload["top_similarity"] == 0.1


def test_try_insert_dedupes_on_exact_query_and_bumps_hit_count():
    """Regression guard: the dedup path must key on the *current* query.

    Previous impl did `.gte("similarity", 0.7)` with no query binding, so
    once any qualifying row existed, every future insert was skipped.
    """
    client = MagicMock()
    # known_unknowns exists
    client.table.return_value.select.return_value.limit.return_value.execute.return_value = Mock(
        data=[]
    )
    # dedupe: existing row for same query
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = Mock(
        data=[{"id": "ku-1", "hit_count": 3}]
    )
    update_chain = client.table.return_value.update.return_value.eq.return_value.execute

    event = {
        "id": "evt-dup",
        "payload": {
            "top_sim": 0.1,
            "query": "recurring gap",
        },
    }
    verdict = {"verdict": "insufficient", "confidence": 0.2}
    fok_batch.try_insert_known_unknown(client, event, verdict, "jarvis")

    update_chain.assert_called()
    update_payload = client.table.return_value.update.call_args.args[0]
    assert update_payload["hit_count"] == 4  # 3 + 1
    assert "last_seen_at" in update_payload

    # No insert should have fired on the dedupe path
    insert_calls = [c for c in client.table.return_value.insert.call_args_list if c.args]
    assert not insert_calls, "insert() must not be called when a dup row exists"


def test_write_event_includes_project():
    """Regression guard: `project` arg must propagate into the emitted event."""
    client = MagicMock()
    fok_batch.write_event(client, {"processed": 3}, "Osasuwu/jarvis")
    insert_call = client.table.return_value.insert.call_args
    assert insert_call is not None
    row = insert_call.args[0]
    assert row["repo"] == "Osasuwu/jarvis"
    assert row["payload"]["project"] == "Osasuwu/jarvis"


def test_check_known_unknowns_exists_uses_table_probe():
    """Previously used non-existent client.rpc("query", ...); now a table probe."""
    client = MagicMock()
    client.table.return_value.select.return_value.limit.return_value.execute.return_value = Mock(
        data=[]
    )
    assert fok_batch.check_known_unknowns_exists(client) is True
    client.table.assert_called_with("known_unknowns")

    # Missing table → False, not raise
    client2 = MagicMock()
    client2.table.return_value.select.return_value.limit.return_value.execute.side_effect = (
        RuntimeError("relation does not exist")
    )
    assert fok_batch.check_known_unknowns_exists(client2) is False


# ---------------------------------------------------------------------------
# Fix A / Test-A1 (#649): main() must fail fast on degenerate config so it never
# persists missing-key 'unknown' verdicts into fok_judgments. --dry-run keeps its
# smoke-test role and must NOT fail fast.
# ---------------------------------------------------------------------------


def test_main_fails_fast_without_api_key_and_writes_nothing(monkeypatch):
    """Non-dry-run with ANTHROPIC_API_KEY unset → exit 1 and zero fok_judgments writes."""
    monkeypatch.setattr("sys.argv", ["fok-batch.py"])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with (
        patch.object(fok_batch, "fetch_events") as mock_fetch,
        patch.object(fok_batch, "write_verdict_to_event") as mock_write,
    ):
        with pytest.raises(SystemExit) as exc:
            fok_batch.main()

    assert exc.value.code == 1
    # Bailed before the fetch/judge loop → no DB reads, no verdict writes.
    mock_fetch.assert_not_called()
    mock_write.assert_not_called()


def test_main_fails_fast_when_httpx_unavailable(monkeypatch):
    """Non-dry-run with httpx import failed → exit 1 (every verdict would be degenerate)."""
    monkeypatch.setattr("sys.argv", ["fok-batch.py"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present-but-httpx-missing")

    original_httpx = fok_batch.httpx
    try:
        fok_batch.httpx = None
        with (
            patch.object(fok_batch, "fetch_events") as mock_fetch,
            patch.object(fok_batch, "write_verdict_to_event") as mock_write,
        ):
            with pytest.raises(SystemExit) as exc:
                fok_batch.main()
        assert exc.value.code == 1
        mock_fetch.assert_not_called()
        mock_write.assert_not_called()
    finally:
        fok_batch.httpx = original_httpx


def test_main_dry_run_does_not_fail_fast_on_missing_key(monkeypatch):
    """--dry-run with the key unset → does NOT exit 1; still fetches/formats/summarizes."""
    monkeypatch.setattr("sys.argv", ["fok-batch.py", "--dry-run"])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "http://localhost")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    with (
        patch("supabase.create_client", return_value=MagicMock()),
        patch.object(fok_batch, "fetch_events", return_value=[]) as mock_fetch,
    ):
        # No SystemExit from the api-key guard; empty fetch → clean return.
        result = fok_batch.main()

    assert result is None
    mock_fetch.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
