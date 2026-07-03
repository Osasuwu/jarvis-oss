"""Unit tests for memory_mark_stale / memory_unmark_stale (M45 S3 #768).

Covers:
- mark_stale(no successor) → expired_at=now(), superseded_by unchanged
- mark_stale(successor='uuid') → superseded_by=uuid, expired_at NOT set
- unmark_stale → expired_at=NULL, superseded_by=NULL
- Returns {action, target_uuid, prior_state}
- outcome_record written with correct pattern_tags
- Host-only gate: refuses when SANDCASTLE_RUN_ID env set
- Missing memory → friendly not-found message
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Stub mcp/supabase/dotenv before importing the handler module.
def _stub_module(name: str, attrs: dict | None = None) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class _FakeTextContent:
    def __init__(self, type: str = "text", text: str = ""):
        self.type = type
        self.text = text


def _noop_decorator(*_args, **_kwargs):
    def decorator(fn):
        return fn

    return decorator


class _FakeServer:
    def __init__(self, *_args, **_kwargs):
        pass

    def list_tools(self):
        return _noop_decorator()

    def call_tool(self):
        return _noop_decorator()


_stub_module(
    "mcp.types",
    {"CallToolResult": MagicMock, "TextContent": _FakeTextContent, "Tool": MagicMock},
)
_stub_module("mcp.server", {"Server": _FakeServer})
_stub_module("mcp.server.stdio", {"stdio_server": MagicMock})
_stub_module("mcp")
try:
    import supabase  # noqa: F401
except ImportError:
    _stub_module("supabase")
try:
    import httpx  # noqa: F401
except ImportError:
    _stub_module("httpx")
_stub_module("dotenv", {"load_dotenv": lambda *_a, **_kw: None})

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-memory"))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

import server as server_module  # noqa: E402
from handlers.memory import (  # noqa: E402
    _handle_memory_mark_stale,
    _handle_memory_unmark_stale,
)


# ---------------------------------------------------------------------------
# Supabase client fake — records update() / select() calls for assertions.
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, parent: "_FakeTable", op: str, payload: dict | None = None):
        self.parent = parent
        self.op = op
        self.payload = payload
        self.filters: list[tuple] = []

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is_", col, val))
        return self

    def execute(self):
        self.parent.calls.append(
            {"op": self.op, "payload": self.payload, "filters": list(self.filters)}
        )
        return MagicMock(data=self.parent.next_data)


class _FakeTable:
    def __init__(self, name: str, store: "_FakeStore"):
        self.name = name
        self.store = store
        self.calls: list[dict] = []
        self.next_data: list[dict] = []

    def select(self, *_args, **_kwargs):
        return _FakeQuery(self, "select")

    def update(self, payload):
        return _FakeQuery(self, "update", payload)

    def insert(self, payload):
        return _FakeQuery(self, "insert", payload)


class _FakeStore:
    def __init__(self):
        self.tables: dict[str, _FakeTable] = {}

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = _FakeTable(name, self)
        return self.tables[name]


@pytest.fixture
def fake_client(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(server_module, "_get_client", lambda: store)
    monkeypatch.setattr(server_module, "_audit_log", lambda *a, **kw: None)
    # Clear sandcastle env so default-path tests run as host
    monkeypatch.delenv("SANDCASTLE_RUN_ID", raising=False)
    return store


def _seed_memory(store: _FakeStore, *, mem_id="mem-uuid-1", project="jarvis"):
    """Pre-load the memories table's select result."""
    store.table("memories").next_data = [
        {
            "id": mem_id,
            "name": "stale-memory",
            "project": project,
            "expired_at": None,
            "superseded_by": None,
        }
    ]


# ---------------------------------------------------------------------------
# AC#3 — mark_stale(no successor) sets expired_at only, NOT superseded_by
# ---------------------------------------------------------------------------


class TestMarkStaleNoSuccessor:
    @pytest.mark.asyncio
    async def test_sets_expired_at(self, fake_client):
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        memory_calls = fake_client.table("memories").calls
        update_calls = [c for c in memory_calls if c["op"] == "update"]
        assert len(update_calls) == 1
        payload = update_calls[0]["payload"]
        assert "expired_at" in payload
        assert payload["expired_at"] is not None
        # Must NOT clobber superseded_by
        assert "superseded_by" not in payload or payload["superseded_by"] is None

    @pytest.mark.asyncio
    async def test_filter_by_name_and_project(self, fake_client):
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        update_call = next(c for c in fake_client.table("memories").calls if c["op"] == "update")
        filter_cols = {(f[0], f[1]) for f in update_call["filters"]}
        assert ("eq", "name") in filter_cols
        assert ("eq", "project") in filter_cols

    @pytest.mark.asyncio
    async def test_global_project_normalized(self, fake_client):
        """project='global' must be normalized to NULL (matches memory_delete behavior)."""
        _seed_memory(fake_client, project=None)
        await _handle_memory_mark_stale(
            {"project": "global", "name": "stale-memory", "reason": "outdated"}
        )
        update_call = next(c for c in fake_client.table("memories").calls if c["op"] == "update")
        # When project is NULL, filter should be is_(project, null) not eq(project, 'global')
        has_is_null = any(f == ("is_", "project", "null") for f in update_call["filters"])
        assert has_is_null


# ---------------------------------------------------------------------------
# AC#4 — mark_stale(successor='uuid') sets superseded_by, NOT expired_at
# ---------------------------------------------------------------------------


class TestMarkStaleWithSuccessor:
    @pytest.mark.asyncio
    async def test_sets_superseded_by(self, fake_client):
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {
                "project": "jarvis",
                "name": "stale-memory",
                "reason": "replaced",
                "successor_uuid": "00000000-0000-0000-0000-000000000099",
            }
        )
        update_call = next(c for c in fake_client.table("memories").calls if c["op"] == "update")
        assert update_call["payload"]["superseded_by"] == "00000000-0000-0000-0000-000000000099"

    @pytest.mark.asyncio
    async def test_clears_expired_at_explicitly(self, fake_client):
        """Supersession is a stronger statement than expiration. When the owner
        upgrades a previously-expired row to superseded (by re-curating with a
        successor), expired_at must be explicitly cleared so the final
        lifecycle state is unambiguous: superseded_by set, expired_at null."""
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {
                "project": "jarvis",
                "name": "stale-memory",
                "reason": "replaced",
                "successor_uuid": "00000000-0000-0000-0000-000000000099",
            }
        )
        update_call = next(c for c in fake_client.table("memories").calls if c["op"] == "update")
        payload = update_call["payload"]
        # expired_at is explicitly null (not just absent — the row may have had it set)
        assert "expired_at" in payload and payload["expired_at"] is None


# ---------------------------------------------------------------------------
# AC#5 — unmark_stale clears BOTH expired_at and superseded_by
# ---------------------------------------------------------------------------


class TestUnmarkStale:
    @pytest.mark.asyncio
    async def test_clears_both_fields(self, fake_client):
        # Pre-seed with both fields set
        fake_client.table("memories").next_data = [
            {
                "id": "mem-uuid-2",
                "name": "revived-memory",
                "project": "jarvis",
                "expired_at": "2026-05-01T12:00:00Z",
                "superseded_by": "00000000-0000-0000-0000-000000000099",
            }
        ]
        await _handle_memory_unmark_stale({"project": "jarvis", "name": "revived-memory"})
        update_call = next(c for c in fake_client.table("memories").calls if c["op"] == "update")
        assert update_call["payload"]["expired_at"] is None
        assert update_call["payload"]["superseded_by"] is None


# ---------------------------------------------------------------------------
# Return shape: {action, target_uuid, prior_state}
# ---------------------------------------------------------------------------


class TestReturnShape:
    @pytest.mark.asyncio
    async def test_mark_stale_returns_action_target_prior(self, fake_client):
        _seed_memory(fake_client, mem_id="abc-123")
        result = await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        text = result[0].text
        # Must reference action + target uuid + prior state somewhere readable
        assert "abc-123" in text
        assert "stale-memory" in text


# ---------------------------------------------------------------------------
# Outcome recording — pattern_tags must include 'memory-hygiene'
# ---------------------------------------------------------------------------


class TestOutcomeRecording:
    @pytest.mark.asyncio
    async def test_mark_stale_records_outcome(self, fake_client):
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        outcome_inserts = [
            c for c in fake_client.table("task_outcomes").calls if c["op"] == "insert"
        ]
        assert len(outcome_inserts) == 1
        payload = outcome_inserts[0]["payload"]
        assert "memory-hygiene" in payload.get("pattern_tags", [])
        assert "manual-curation" in payload.get("pattern_tags", [])

    @pytest.mark.asyncio
    async def test_unmark_stale_records_revival_outcome(self, fake_client):
        fake_client.table("memories").next_data = [
            {
                "id": "mem-uuid-2",
                "name": "revived-memory",
                "project": "jarvis",
                "expired_at": "2026-05-01T12:00:00Z",
                "superseded_by": None,
            }
        ]
        await _handle_memory_unmark_stale({"project": "jarvis", "name": "revived-memory"})
        outcome_inserts = [
            c for c in fake_client.table("task_outcomes").calls if c["op"] == "insert"
        ]
        assert len(outcome_inserts) == 1
        assert "memory-hygiene" in outcome_inserts[0]["payload"]["pattern_tags"]
        assert "revival" in outcome_inserts[0]["payload"]["pattern_tags"]


# ---------------------------------------------------------------------------
# AC#2 — Host-only gate: refuses when running in sandcastle container
# ---------------------------------------------------------------------------


class TestHostOnlyGate:
    @pytest.mark.asyncio
    async def test_mark_stale_refuses_when_sandcastle_env_set(self, fake_client, monkeypatch):
        monkeypatch.setenv("SANDCASTLE_RUN_ID", "test-run-abc")
        _seed_memory(fake_client)
        result = await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        text = result[0].text.lower()
        assert "host" in text or "sandcastle" in text or "refused" in text
        # Critically: no UPDATE issued
        update_calls = [c for c in fake_client.table("memories").calls if c["op"] == "update"]
        assert update_calls == []

    @pytest.mark.asyncio
    async def test_unmark_stale_refuses_when_sandcastle_env_set(self, fake_client, monkeypatch):
        monkeypatch.setenv("SANDCASTLE_RUN_ID", "test-run-abc")
        result = await _handle_memory_unmark_stale({"project": "jarvis", "name": "revived-memory"})
        text = result[0].text.lower()
        assert "host" in text or "sandcastle" in text or "refused" in text
        update_calls = [c for c in fake_client.table("memories").calls if c["op"] == "update"]
        assert update_calls == []


# ---------------------------------------------------------------------------
# Not-found path
# ---------------------------------------------------------------------------


class TestNotFound:
    @pytest.mark.asyncio
    async def test_mark_stale_missing_memory(self, fake_client):
        # No seed → select returns []
        fake_client.table("memories").next_data = []
        result = await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "nope", "reason": "x"}
        )
        assert "not found" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_unmark_stale_missing_memory(self, fake_client):
        fake_client.table("memories").next_data = []
        result = await _handle_memory_unmark_stale({"project": "jarvis", "name": "nope"})
        assert "not found" in result[0].text.lower()


# ---------------------------------------------------------------------------
# Soft-deleted rows are excluded (matches _handle_get / _handle_delete default)
# ---------------------------------------------------------------------------


class TestDeletedAtExcluded:
    @pytest.mark.asyncio
    async def test_mark_stale_select_filters_deleted_at(self, fake_client):
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        select_call = next(c for c in fake_client.table("memories").calls if c["op"] == "select")
        assert ("is_", "deleted_at", "null") in select_call["filters"]

    @pytest.mark.asyncio
    async def test_mark_stale_update_filters_deleted_at(self, fake_client):
        _seed_memory(fake_client)
        await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        update_call = next(c for c in fake_client.table("memories").calls if c["op"] == "update")
        assert ("is_", "deleted_at", "null") in update_call["filters"]

    @pytest.mark.asyncio
    async def test_unmark_stale_filters_deleted_at(self, fake_client):
        fake_client.table("memories").next_data = [
            {
                "id": "mem-uuid-2",
                "name": "revived",
                "project": "jarvis",
                "expired_at": "2026-05-01T12:00:00Z",
                "superseded_by": None,
            }
        ]
        await _handle_memory_unmark_stale({"project": "jarvis", "name": "revived"})
        select_call = next(c for c in fake_client.table("memories").calls if c["op"] == "select")
        assert ("is_", "deleted_at", "null") in select_call["filters"]


# ---------------------------------------------------------------------------
# Response verbs: past-tense user-facing + field-changed annotation
# ---------------------------------------------------------------------------


class TestResponseFormat:
    @pytest.mark.asyncio
    async def test_mark_expire_uses_past_tense(self, fake_client):
        _seed_memory(fake_client)
        result = await _handle_memory_mark_stale(
            {"project": "jarvis", "name": "stale-memory", "reason": "outdated"}
        )
        text = result[0].text
        assert "Expired" in text
        assert "expired_at set" in text

    @pytest.mark.asyncio
    async def test_mark_supersede_uses_past_tense(self, fake_client):
        _seed_memory(fake_client)
        result = await _handle_memory_mark_stale(
            {
                "project": "jarvis",
                "name": "stale-memory",
                "reason": "replaced",
                "successor_uuid": "00000000-0000-0000-0000-000000000099",
            }
        )
        text = result[0].text
        assert "Superseded" in text
        assert "superseded_by set" in text


# ---------------------------------------------------------------------------
# AC#1 — Tool schemas + dispatch registered in tools_schema.py + server.py
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_mark_stale_tool_in_schema(self):
        from tools_schema import tool_definitions

        # Tool is stubbed to MagicMock; instances are recorded as positional/kw args.
        # The simplest contract test: the function executes and the call records
        # exist with our names. Use string check on the source instead — robust.
        # Read the source once and assert presence of the two tool names.
        import inspect

        src = inspect.getsource(tool_definitions)
        assert "memory_mark_stale" in src
        assert "memory_unmark_stale" in src
        # Required fields surfaced
        assert "successor_uuid" in src
        assert "reason" in src

    def test_server_dispatches_mark_unmark(self):
        import inspect

        src = inspect.getsource(server_module.call_tool)
        assert 'name == "memory_mark_stale"' in src
        assert 'name == "memory_unmark_stale"' in src
