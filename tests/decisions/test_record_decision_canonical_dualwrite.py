"""Integration tests for record_decision dual-write (#477).

The handler now writes to BOTH the legacy ``episodes`` table AND the
canonical ``events_canonical`` substrate. These tests verify:

1. Both tables receive an insert on a successful call.
2. OTel-shaped keys appear in the events_canonical payload only when
   ``llm`` metadata is supplied.
3. A failure in the events_canonical write does NOT propagate — the
   episodes write is still surfaced as success.
4. Without ``llm`` metadata, the events_canonical row has no cost
   columns (substrate uses NULL — design 1-pager §1).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server import _handle_record_decision
import events_canonical as events_canonical_mod


@pytest.fixture(autouse=True)
def _isolate_buffer() -> None:
    events_canonical_mod._buffer_clear_for_test()
    yield
    events_canonical_mod._buffer_clear_for_test()


# ---------------------------------------------------------------------------
# Lightweight Supabase client fakes (contract-style, no deep mock chains)
# ---------------------------------------------------------------------------


class _FakeSelectBuilder:
    """Replaces MagicMock chains for ``table.select().eq(...).execute()``.

    Every builder method returns ``self`` so any call order works — the
    only method with real behavior is ``.execute()``.
    """

    def __init__(self, data: list | None = None) -> None:
        self._data = data or []

    def eq(self, *a, **kw) -> _FakeSelectBuilder:
        return self

    def is_(self, *a, **kw) -> _FakeSelectBuilder:
        return self

    def order(self, *a, **kw) -> _FakeSelectBuilder:
        return self

    def limit(self, *a, **kw) -> _FakeSelectBuilder:
        return self

    def execute(self):
        return MagicMock(data=self._data)


class _FakeTable:
    """Lightweight fake for a Supabase table builder.

    Records every ``.insert(payload)`` call in an ordered list so tests
    can inspect what was written to each table.
    """

    def __init__(self, name: str, *,
                 inserted_episode_id: str = "ep-123",
                 canonical_raises: Exception | None = None,
                 ) -> None:
        self._name = name
        self._insert_payloads: list[dict] = []
        self._canonical_raises = canonical_raises
        self._inserted_episode_id = inserted_episode_id

    def insert(self, payload: dict):
        self._insert_payloads.append(payload)
        if self._name == "events_canonical" and self._canonical_raises is not None:
            raise self._canonical_raises
        return MagicMock(
            execute=lambda: MagicMock(
                data=[{"id": self._inserted_episode_id}]
            )
        )

    def select(self, *args, **kwargs) -> _FakeSelectBuilder:
        return _FakeSelectBuilder()


class _FakeClient:
    """Contract-style Supabase client for dual-write tests.

    Returns one ``_FakeTable`` per table name, cached so that
    multiple ``.table(name)`` calls in the same handler invocation
    return the same instance — insert payloads are accumulated.
    """

    def __init__(self, *,
                 inserted_episode_id: str = "ep-123",
                 canonical_raises: Exception | None = None,
                 ) -> None:
        self._tables: dict[str, _FakeTable] = {}
        self._inserted_episode_id = inserted_episode_id
        self._canonical_raises = canonical_raises

    def table(self, name: str) -> _FakeTable:
        if name not in self._tables:
            self._tables[name] = _FakeTable(
                name,
                inserted_episode_id=self._inserted_episode_id,
                canonical_raises=self._canonical_raises,
            )
        return self._tables[name]


def _calls_to_table(client, name: str) -> list[dict]:
    """Return the list of insert payloads sent to the table ``name``."""
    tables = getattr(client, "_tables", {})
    table = tables.get(name)
    if table is None:
        return []
    return table._insert_payloads


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDualWriteHappyPath:
    @pytest.mark.asyncio
    async def test_writes_to_both_tables(self, monkeypatch):
        client = _FakeClient()
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "Pick X",
                "rationale": "Because of Y",
                "reversibility": "reversible",
            }
        )
        assert "Decision recorded" in result[0].text

        episode_inserts = _calls_to_table(client, "episodes")
        canonical_inserts = _calls_to_table(client, "events_canonical")
        assert len(episode_inserts) == 1, f"expected 1 episodes insert, got {len(episode_inserts)}"
        assert len(canonical_inserts) == 1, f"expected 1 events_canonical insert, got {len(canonical_inserts)}"

        # Episodes path preserved (legacy contract).
        ep = episode_inserts[0]
        assert ep["kind"] == "decision_made"
        assert ep["payload"]["decision"] == "Pick X"

        # Canonical row carries action + actor + trace_id.
        ec = canonical_inserts[0]
        assert ec["action"] == "decision_made"
        assert ec["actor"]  # default 'skill:unknown' or whatever passed
        assert "trace_id" in ec and isinstance(ec["trace_id"], str)
        assert ec["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_canonical_payload_includes_episode_id_link(self, monkeypatch):
        client = _FakeClient(inserted_episode_id="ep-link-test")
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
            }
        )
        canonical = _calls_to_table(client, "events_canonical")[0]
        assert canonical["payload"]["episode_id"] == "ep-link-test"


class TestOTelKeysFromLLMMetadata:
    @pytest.mark.asyncio
    async def test_otel_keys_present_when_llm_provided(self, monkeypatch):
        client = _FakeClient()
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "llm": {
                    "model": "claude-haiku-4-5-20251001",
                    "input_tokens": 1234,
                    "output_tokens": 567,
                    "cost_usd": 0.0125,
                    "provider": "anthropic",
                    "operation": "chat",
                },
            }
        )
        canonical = _calls_to_table(client, "events_canonical")[0]
        p = canonical["payload"]
        assert p["gen_ai.request.model"] == "claude-haiku-4-5-20251001"
        assert p["gen_ai.usage.input_tokens"] == 1234
        assert p["gen_ai.usage.output_tokens"] == 567
        assert p["gen_ai.usage.cost_usd"] == 0.0125
        assert p["gen_ai.provider.name"] == "anthropic"
        assert p["gen_ai.operation.name"] == "chat"
        # cost columns populated
        assert canonical["cost_tokens"] == 1234 + 567
        assert canonical["cost_usd"] == 0.0125

    @pytest.mark.asyncio
    async def test_otel_keys_absent_when_no_llm_metadata(self, monkeypatch):
        client = _FakeClient()
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
            }
        )
        canonical = _calls_to_table(client, "events_canonical")[0]
        p = canonical["payload"]
        for key in (
            "gen_ai.request.model",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.cost_usd",
            "gen_ai.provider.name",
            "gen_ai.operation.name",
        ):
            assert key not in p, f"OTel key {key!r} unexpectedly set"
        assert "cost_tokens" not in canonical
        assert "cost_usd" not in canonical


class TestCanonicalFailureDoesNotBreakEpisodeWrite:
    @pytest.mark.asyncio
    async def test_substrate_failure_returns_success(self, monkeypatch):
        client = _FakeClient(
            canonical_raises=RuntimeError("connection dropped")
        )
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
            }
        )
        # Episode write succeeded — caller sees success.
        assert "Decision recorded" in result[0].text

        # Episode insert happened. Canonical insert was attempted.
        assert _calls_to_table(client, "episodes")
        # Buffer absorbed the failed canonical event.
        assert events_canonical_mod._buffer_len_for_test() == 1
