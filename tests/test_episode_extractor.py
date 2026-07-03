"""Unit tests for mcp-memory/episode_extractor.py and scripts/capture-episode.py.

Covers the deterministic pieces: prompt assembly, response parsing, batch
orchestration with a mocked Supabase client. Network paths (Voyage + Haiku)
are exercised via stub HTTP clients to keep the suite hermetic.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Stub external deps the way test_memory_server.py does — so this test file
# can run standalone even when httpx / supabase / dotenv aren't installed.
# ---------------------------------------------------------------------------

try:
    import httpx  # noqa: F401
except ImportError:
    sys.modules["httpx"] = types.ModuleType("httpx")

try:
    import supabase  # noqa: F401
except ImportError:
    sys.modules["supabase"] = types.ModuleType("supabase")

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", _dotenv)

# Add mcp-memory + scripts to sys.path.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "mcp-memory"))
sys.path.insert(0, str(_REPO / "scripts"))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

import episode_extractor as ee  # noqa: E402


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_unchanged(self):
        assert ee._truncate("hello") == "hello"

    def test_empty(self):
        assert ee._truncate("") == ""

    def test_truncates_with_ellipsis(self):
        text = "x" * (ee.MAX_PAYLOAD_CHARS_PER_EPISODE + 100)
        out = ee._truncate(text)
        assert out.endswith("…")
        assert len(out) == ee.MAX_PAYLOAD_CHARS_PER_EPISODE + 1


# ---------------------------------------------------------------------------
# _canonical_embed_text
# ---------------------------------------------------------------------------


class TestCanonicalEmbedText:
    def test_name_underscores_replaced(self):
        out = ee._canonical_embed_text("my_memory_name", "", [], "body")
        assert "my memory name" in out
        assert "my_memory_name" not in out

    def test_tags_rendered(self):
        out = ee._canonical_embed_text("n", "", ["a", "b"], "body")
        assert "tags: a, b" in out

    def test_empty_inputs(self):
        assert ee._canonical_embed_text("", "", [], "") == ""

    def test_all_fields_ordered(self):
        out = ee._canonical_embed_text("name", "desc", ["t1"], "content")
        lines = out.split("\n")
        assert lines[0] == "name"
        assert lines[1] == "tags: t1"
        assert lines[2] == "desc"
        assert lines[3] == "content"


# ---------------------------------------------------------------------------
# _render_episode + build_synthesis_user_message
# ---------------------------------------------------------------------------


class TestRenderEpisode:
    def test_includes_all_fields(self):
        out = ee._render_episode(
            {
                "id": "ep1",
                "actor": "session:x",
                "kind": "user_message",
                "created_at": "2026-04-18T10:00:00Z",
                "payload": {"text": "hello"},
            }
        )
        assert "ep1" in out
        assert "session:x" in out
        assert "user_message" in out
        assert "2026-04-18T10:00:00Z" in out
        assert '"text"' in out and "hello" in out

    def test_truncates_huge_payload(self):
        out = ee._render_episode(
            {
                "id": "ep",
                "actor": "a",
                "kind": "tool_call",
                "created_at": "",
                "payload": {"blob": "x" * 5000},
            }
        )
        assert len(out) < 2000  # well under the raw payload size
        assert "…" in out

    def test_non_dict_payload(self):
        out = ee._render_episode(
            {
                "id": "ep",
                "actor": "a",
                "kind": "observation",
                "created_at": "",
                "payload": "raw string",
            }
        )
        assert "raw string" in out


class TestBuildSynthesisUserMessage:
    def test_empty_batch(self):
        assert "empty batch" in ee.build_synthesis_user_message([])

    def test_multiple_episodes_separated(self):
        eps = [
            {"id": "e1", "actor": "a", "kind": "user_message", "payload": {"x": 1}},
            {"id": "e2", "actor": "b", "kind": "decision", "payload": {"y": 2}},
        ]
        out = ee.build_synthesis_user_message(eps)
        assert "e1" in out
        assert "e2" in out
        assert out.startswith("EPISODES")


# ---------------------------------------------------------------------------
# parse_synthesis_response
# ---------------------------------------------------------------------------


class TestParseSynthesisResponse:
    def test_empty_string(self):
        assert ee.parse_synthesis_response("") == []

    def test_no_json(self):
        assert ee.parse_synthesis_response("no json here") == []

    def test_malformed_json(self):
        assert ee.parse_synthesis_response("{not valid") == []

    def test_empty_candidates(self):
        assert ee.parse_synthesis_response('{"candidates": []}') == []

    def test_candidates_not_a_list(self):
        assert ee.parse_synthesis_response('{"candidates": "oops"}') == []

    def test_valid_single_candidate(self):
        resp = json.dumps(
            {
                "candidates": [
                    {
                        "name": "test_mem",
                        "type": "project",
                        "description": "a description",
                        "content": "the content",
                        "tags": ["tag1", "tag2"],
                    }
                ]
            }
        )
        out = ee.parse_synthesis_response(resp)
        assert len(out) == 1
        c = out[0]
        assert c.name == "test_mem"
        assert c.type == "project"
        assert c.tags == ["tag1", "tag2"]

    def test_drops_invalid_type(self):
        resp = json.dumps(
            {
                "candidates": [
                    {"name": "good", "type": "project", "description": "", "content": "c"},
                    {"name": "bad", "type": "nonsense", "description": "", "content": "c"},
                ]
            }
        )
        out = ee.parse_synthesis_response(resp)
        assert len(out) == 1
        assert out[0].name == "good"

    def test_drops_empty_name_or_content(self):
        resp = json.dumps(
            {
                "candidates": [
                    {"name": "", "type": "project", "content": "c"},
                    {"name": "x", "type": "project", "content": ""},
                    {"name": "y", "type": "project", "content": "has content"},
                ]
            }
        )
        out = ee.parse_synthesis_response(resp)
        assert len(out) == 1
        assert out[0].name == "y"

    def test_tolerates_prose_around_json(self):
        resp = (
            "Here is the output:\n"
            + json.dumps({"candidates": [{"name": "x", "type": "user", "content": "c"}]})
            + "\n\nDone."
        )
        out = ee.parse_synthesis_response(resp)
        assert len(out) == 1

    def test_tag_normalization(self):
        resp = json.dumps(
            {
                "candidates": [
                    {
                        "name": "x",
                        "type": "user",
                        "content": "c",
                        "tags": ["  TAG1 ", "", "Tag2", 42],
                    }
                ]
            }
        )
        out = ee.parse_synthesis_response(resp)
        assert out[0].tags == ["tag1", "tag2", "42"]

    def test_tag_hard_cap(self):
        resp = json.dumps(
            {
                "candidates": [
                    {
                        "name": "x",
                        "type": "user",
                        "content": "c",
                        "tags": [f"tag{i}" for i in range(20)],
                    }
                ]
            }
        )
        out = ee.parse_synthesis_response(resp)
        assert len(out[0].tags) == 10


# ---------------------------------------------------------------------------
# synthesize_candidates — no API key short-circuit
# ---------------------------------------------------------------------------


class TestSynthesizeCandidatesNoKey:
    def test_returns_none_without_api_key(self, monkeypatch):
        """Missing API key is a failure signal (None), not a legit-empty []
        — the caller must leave the batch unprocessed so a later run retries."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = asyncio.run(
            ee.synthesize_candidates(
                [{"id": "e", "actor": "a", "kind": "user_message", "payload": {}}]
            )
        )
        assert out is None

    def test_returns_empty_on_empty_batch(self):
        out = asyncio.run(ee.synthesize_candidates([]))
        assert out == []


# ---------------------------------------------------------------------------
# process_batch — mocked client integration
# ---------------------------------------------------------------------------


class _MockResp:
    def __init__(self, data):
        self.data = data


class _MockQuery:
    """Minimal fluent-API mock for the Supabase python client chaining pattern."""

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    def select(self, *a, **k):
        self.calls.append(("select", a, k))
        return self

    def is_(self, *a, **k):
        self.calls.append(("is_", a, k))
        return self

    def eq(self, *a, **k):
        self.calls.append(("eq", a, k))
        return self

    def order(self, *a, **k):
        self.calls.append(("order", a, k))
        return self

    def limit(self, *a, **k):
        self.calls.append(("limit", a, k))
        return self

    def in_(self, *a, **k):
        self.calls.append(("in_", a, k))
        return self

    def update(self, *a, **k):
        self.calls.append(("update", a, k))
        return self

    def insert(self, *a, **k):
        self.calls.append(("insert", a, k))
        return self

    def execute(self):
        return self.result


class _MockTable:
    """Per-table mock that hands out fresh _MockQuery instances and records them."""

    def __init__(self):
        self.fetch_result = _MockResp([])
        self.insert_result = _MockResp([])
        self.update_result = _MockResp([])
        self.queries: list[_MockQuery] = []

    def _q(self, result=None):
        q = _MockQuery(result if result is not None else _MockResp([]))
        self.queries.append(q)
        return q

    def select(self, *a, **k):
        q = self._q(self.fetch_result)
        return q.select(*a, **k)

    def insert(self, *a, **k):
        q = self._q(self.insert_result)
        return q.insert(*a, **k)

    def update(self, *a, **k):
        q = self._q(self.update_result)
        return q.update(*a, **k)


class _MockClient:
    def __init__(self):
        self.tables: dict[str, _MockTable] = {
            "episodes": _MockTable(),
            "memories": _MockTable(),
            "memory_links": _MockTable(),
            "memory_review_queue": _MockTable(),
        }
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name: str) -> _MockTable:
        return self.tables.setdefault(name, _MockTable())

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        # find_similar_memories returns no neighbors by default → skips classifier
        return _MockQuery(_MockResp([]))


class TestProcessBatchEmpty:
    def test_empty_backlog_returns_noop_result(self):
        client = _MockClient()
        # fetch_unprocessed returns [] by default
        result = asyncio.run(ee.process_batch(client, batch_size=5))
        assert result.episode_ids == []
        assert result.candidates_synthesized == 0
        assert result.candidates_inserted == 0


class TestProcessBatchWithEpisodes:
    def test_marks_processed_when_synthesis_empty(self, monkeypatch):
        """With an API key set and synthesis returning []: episodes are marked
        processed (treated as legitimately nothing to extract)."""
        client = _MockClient()
        client.tables["episodes"].fetch_result = _MockResp(
            [
                {
                    "id": "e1",
                    "actor": "test",
                    "kind": "user_message",
                    "payload": {},
                    "created_at": "2026-04-18",
                },
            ]
        )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        async def fake_synth(episodes, **kwargs):
            return []

        monkeypatch.setattr(ee, "synthesize_candidates", fake_synth)

        result = asyncio.run(ee.process_batch(client, batch_size=5))

        assert result.episode_ids == ["e1"]
        assert result.candidates_synthesized == 0
        # Verify mark_processed ran: one update on the episodes table
        ep_ops = [q.calls for q in client.tables["episodes"].queries]
        assert any(any(c[0] == "update" for c in call_list) for call_list in ep_ops)

    def test_does_not_mark_processed_when_synthesis_fails(self, monkeypatch):
        """Synthesis failure (None return) must leave episodes unprocessed so
        a later run can retry. We simulate this via missing API key — the
        same path handles network errors."""
        client = _MockClient()
        client.tables["episodes"].fetch_result = _MockResp(
            [
                {
                    "id": "e1",
                    "actor": "t",
                    "kind": "user_message",
                    "payload": {},
                    "created_at": "2026-04-18",
                },
            ]
        )

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = asyncio.run(ee.process_batch(client, batch_size=5))

        assert result.episode_ids == ["e1"]
        assert "synthesis failed" in " ".join(result.errors)
        # Episode update must NOT have been called.
        ep_ops = [q.calls for q in client.tables["episodes"].queries]
        assert not any(any(c[0] == "update" for c in call_list) for call_list in ep_ops)

    def test_does_not_mark_processed_when_insert_errors(self, monkeypatch):
        """If a candidate fails to insert (_insert_candidate returns None),
        the batch should be left unprocessed so a later run can retry the
        whole pipeline — we don't want to silently lose episodes."""
        client = _MockClient()
        client.tables["episodes"].fetch_result = _MockResp(
            [
                {
                    "id": "e1",
                    "actor": "t",
                    "kind": "user_message",
                    "payload": {},
                    "created_at": "2026-04-18",
                },
            ]
        )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        async def fake_synth(episodes, **kwargs):
            return [ee.Candidate("n", "project", "", "c", [])]

        async def fake_embed(text):
            return None

        def fake_insert(client, candidate, embedding, source_provenance):
            return None  # simulate insert failure

        monkeypatch.setattr(ee, "synthesize_candidates", fake_synth)
        monkeypatch.setattr(ee, "_embed", fake_embed)
        monkeypatch.setattr(ee, "_insert_candidate", fake_insert)

        result = asyncio.run(ee.process_batch(client, batch_size=5))

        assert result.candidates_inserted == 0
        assert any("failed to insert" in e for e in result.errors)
        ep_ops = [q.calls for q in client.tables["episodes"].queries]
        assert not any(any(c[0] == "update" for c in call_list) for call_list in ep_ops)

    def test_inserts_candidates_with_episode_provenance(self, monkeypatch):
        """End-to-end: fake synthesis returns candidates, extractor inserts
        each with source_provenance='episode:<first-id>'."""
        client = _MockClient()
        client.tables["episodes"].fetch_result = _MockResp(
            [
                {
                    "id": "ep-alpha",
                    "actor": "test",
                    "kind": "user_message",
                    "payload": {"text": "hi"},
                    "created_at": "2026-04-18",
                },
                {
                    "id": "ep-beta",
                    "actor": "test",
                    "kind": "decision",
                    "payload": {"choice": "X"},
                    "created_at": "2026-04-18",
                },
            ]
        )
        # Pretend the candidate is new (no existing memory with this name).
        client.tables["memories"].fetch_result = _MockResp([])
        client.tables["memories"].insert_result = _MockResp([{"id": "mem-1"}])

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        async def fake_synth(episodes, **kwargs):
            return [
                ee.Candidate(
                    name="test_candidate",
                    type="project",
                    description="test",
                    content="candidate content",
                    tags=["phase-4"],
                )
            ]

        async def fake_embed(text):
            return None  # skip neighbor lookup

        monkeypatch.setattr(ee, "synthesize_candidates", fake_synth)
        monkeypatch.setattr(ee, "_embed", fake_embed)

        result = asyncio.run(ee.process_batch(client, batch_size=5))

        assert result.candidates_synthesized == 1
        assert result.candidates_inserted == 1
        assert result.episode_ids == ["ep-alpha", "ep-beta"]

        # Locate the insert call on the memories table and check provenance.
        inserts = []
        for q in client.tables["memories"].queries:
            for call in q.calls:
                if call[0] == "insert":
                    inserts.append(call[1][0])  # the dict passed to insert()
        assert len(inserts) == 1
        assert inserts[0]["source_provenance"] == "episode:ep-alpha"
        assert inserts[0]["name"] == "test_candidate"
        assert inserts[0]["type"] == "project"

    def test_dry_run_does_not_insert(self, monkeypatch):
        client = _MockClient()
        client.tables["episodes"].fetch_result = _MockResp(
            [
                {
                    "id": "e1",
                    "actor": "t",
                    "kind": "user_message",
                    "payload": {},
                    "created_at": "2026-04-18",
                },
            ]
        )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        async def fake_synth(episodes, **kwargs):
            return [ee.Candidate("n", "project", "", "c", [])]

        async def fake_embed(text):
            return None

        monkeypatch.setattr(ee, "synthesize_candidates", fake_synth)
        monkeypatch.setattr(ee, "_embed", fake_embed)

        result = asyncio.run(ee.process_batch(client, batch_size=5, dry_run=True))

        assert result.candidates_inserted == 1  # counted as if inserted
        # But no actual insert calls on memories.
        for q in client.tables["memories"].queries:
            assert not any(c[0] == "insert" for c in q.calls)
        # And episodes not marked processed.
        for q in client.tables["episodes"].queries:
            assert not any(c[0] == "update" for c in q.calls)


# ---------------------------------------------------------------------------
# capture-episode script
# ---------------------------------------------------------------------------


# The script has a dash in its filename — import it via importlib.
import importlib.util  # noqa: E402

_capture_path = _REPO / "scripts" / "capture-episode.py"
_spec = importlib.util.spec_from_file_location("capture_episode", _capture_path)
capture_episode = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture_episode)


class TestCaptureLoadPayload:
    def test_inline_json(self):
        args = capture_episode._parse_args(
            [
                "--actor",
                "test",
                "--kind",
                "user_message",
                "--payload",
                '{"k": 1}',
            ]
        )
        assert capture_episode._load_payload(args) == {"k": 1}

    def test_missing_payload_returns_empty_dict(self):
        args = capture_episode._parse_args(
            [
                "--actor",
                "test",
                "--kind",
                "user_message",
            ]
        )
        assert capture_episode._load_payload(args) == {}

    def test_stdin_json(self, monkeypatch):
        args = capture_episode._parse_args(
            [
                "--actor",
                "test",
                "--kind",
                "user_message",
                "--from-stdin",
            ]
        )
        monkeypatch.setattr("sys.stdin", types.SimpleNamespace(read=lambda: '{"k": 2}'))
        assert capture_episode._load_payload(args) == {"k": 2}

    def test_stdin_raw_text_wrapped(self, monkeypatch):
        """Non-JSON stdin gets wrapped as {"text": ...} so the jsonb
        constraint still holds — this is the common hook case (raw prompt)."""
        args = capture_episode._parse_args(
            [
                "--actor",
                "test",
                "--kind",
                "user_message",
                "--from-stdin",
            ]
        )
        monkeypatch.setattr(
            "sys.stdin",
            types.SimpleNamespace(read=lambda: "not valid json but still useful"),
        )
        assert capture_episode._load_payload(args) == {"text": "not valid json but still useful"}

    def test_rejects_invalid_kind(self):
        with pytest.raises(SystemExit):
            capture_episode._parse_args(
                [
                    "--actor",
                    "test",
                    "--kind",
                    "nonsense",
                ]
            )
