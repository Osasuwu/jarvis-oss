"""Unit tests for mcp-memory/server.py — recall pipeline: link expansion,
lifecycle filters, outcome memory ID passthrough.

conftest.py handles the sys.modules stubs for MCP SDK + Supabase before
this file loads, so `from server import` works without the real deps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from server import (
    _expand_with_links,
    _handle_outcome_record,
    _handle_outcome_update,
    _hybrid_recall,
    _keyword_recall,
)
import server as server_module


# ---------------------------------------------------------------------------
# _expand_with_links (async, mocked Supabase)
# ---------------------------------------------------------------------------


class TestExpandWithLinks:
    """Graph traversal fetches 1-hop linked memories."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_returns_linked_memories(self, mock_client):
        mock_client.rpc.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "linked-1",
                    "name": "linked_mem",
                    "link_type": "related",
                    "link_strength": 0.75,
                },
            ]
        )
        result = await _expand_with_links(mock_client, ["source-id"])
        assert len(result) == 1
        assert result[0]["id"] == "linked-1"

    @pytest.mark.asyncio
    async def test_empty_when_no_links(self, mock_client):
        mock_client.rpc.return_value.execute.return_value = MagicMock(data=[])
        result = await _expand_with_links(mock_client, ["source-id"])
        assert result == []

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self, mock_client):
        mock_client.rpc.side_effect = Exception("DB error")
        result = await _expand_with_links(mock_client, ["source-id"])
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_memory_ids_to_rpc(self, mock_client):
        mock_client.rpc.return_value.execute.return_value = MagicMock(data=[])
        await _expand_with_links(mock_client, ["id-1", "id-2"])
        mock_client.rpc.assert_called_once_with(
            "get_linked_memories",
            {
                "memory_ids": ["id-1", "id-2"],
                "link_types": None,
                "show_history": False,
                "include_unreviewed": False,
            },
        )

    @pytest.mark.asyncio
    async def test_passes_show_history_to_rpc(self, mock_client):
        mock_client.rpc.return_value.execute.return_value = MagicMock(data=[])
        await _expand_with_links(mock_client, ["id-1"], show_history=True)
        mock_client.rpc.assert_called_once_with(
            "get_linked_memories",
            {
                "memory_ids": ["id-1"],
                "link_types": None,
                "show_history": True,
                "include_unreviewed": False,
            },
        )

    @pytest.mark.asyncio
    async def test_passes_include_unreviewed_to_rpc(self, mock_client):
        mock_client.rpc.return_value.execute.return_value = MagicMock(data=[])
        await _expand_with_links(mock_client, ["id-1"], include_unreviewed=True)
        mock_client.rpc.assert_called_once_with(
            "get_linked_memories",
            {
                "memory_ids": ["id-1"],
                "link_types": None,
                "show_history": False,
                "include_unreviewed": True,
            },
        )


# ---------------------------------------------------------------------------
# #284: memory_recall must exclude soft-deleted + superseded + expired rows
# ---------------------------------------------------------------------------


class TestRecallLifecycleFilters:
    """Regression (Osasuwu/jarvis#284): memory_recall surfaced soft-deleted
    rows even when show_history=false."""

    @staticmethod
    def _fluent_query():
        q = MagicMock()
        for method in ("select", "is_", "or_", "eq", "limit", "order", "filter"):
            getattr(q, method).return_value = q
        q.execute.return_value = MagicMock(data=[])
        return q

    @pytest.mark.asyncio
    async def test_keyword_recall_applies_all_lifecycle_filters(self):
        query = self._fluent_query()
        client = MagicMock()
        client.table.return_value = query

        await _keyword_recall(client, "anything", project="jarvis", mem_type=None, limit=10)

        is_args = [tuple(call.args) for call in query.is_.call_args_list]
        assert ("deleted_at", "null") in is_args
        assert ("expired_at", "null") in is_args
        assert ("superseded_by", "null") in is_args

        or_filters = [call.args[0] for call in query.or_.call_args_list]
        assert not any("valid_to" in f for f in or_filters)

        select_args = [call.args[0] for call in query.select.call_args_list]
        assert any("valid_to" in s for s in select_args)

    @pytest.mark.asyncio
    async def test_keyword_recall_brief_mode_also_filters(self):
        query = self._fluent_query()
        client = MagicMock()
        client.table.return_value = query

        await _keyword_recall(client, "anything", project=None, mem_type=None, limit=5, brief=True)

        is_args = [tuple(call.args) for call in query.is_.call_args_list]
        assert ("deleted_at", "null") in is_args
        assert ("expired_at", "null") in is_args
        assert ("superseded_by", "null") in is_args

        select_args = [call.args[0] for call in query.select.call_args_list]
        assert any("valid_to" in s for s in select_args)

    @pytest.mark.asyncio
    async def test_keyword_recall_filters_past_valid_to(self):
        now = datetime.now(timezone.utc)
        past = (now - timedelta(days=1)).isoformat()
        future = (now + timedelta(days=30)).isoformat()

        rows = [
            {
                "name": "live_null_vt",
                "type": "project",
                "project": "jarvis",
                "description": "d",
                "tags": [],
                "updated_at": now.isoformat(),
                "valid_to": None,
            },
            {
                "name": "live_future_vt",
                "type": "project",
                "project": "jarvis",
                "description": "d",
                "tags": [],
                "updated_at": now.isoformat(),
                "valid_to": future,
            },
            {
                "name": "tombstoned_past_vt",
                "type": "project",
                "project": "jarvis",
                "description": "d",
                "tags": [],
                "updated_at": now.isoformat(),
                "valid_to": past,
            },
        ]

        query = self._fluent_query()
        query.execute.return_value = MagicMock(data=rows)
        client = MagicMock()
        client.table.return_value = query

        result = await _keyword_recall(
            client, "anything", project="jarvis", mem_type=None, limit=10, brief=True
        )

        text = result[0].text
        assert "live_null_vt" in text
        assert "live_future_vt" in text
        assert "tombstoned_past_vt" not in text
        assert "Found 2 memories" in text

    @pytest.mark.asyncio
    async def test_keyword_recall_all_rows_tombstoned_returns_empty(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = [
            {
                "name": "dead1",
                "type": "project",
                "project": "jarvis",
                "description": "d",
                "tags": [],
                "updated_at": past,
                "valid_to": past,
            },
        ]

        query = self._fluent_query()
        query.execute.return_value = MagicMock(data=rows)
        client = MagicMock()
        client.table.return_value = query

        result = await _keyword_recall(
            client, "anything", project="jarvis", mem_type=None, limit=10
        )
        assert result[0].text == "No memories found."

    @pytest.mark.asyncio
    async def test_hybrid_recall_defaults_show_history_false(self, monkeypatch):
        client = MagicMock()
        client.rpc.return_value.execute.return_value = MagicMock(data=[])

        async def _stub_embed(_text):
            return [0.0] * 512

        monkeypatch.setattr(server_module, "_embed_query", _stub_embed)

        await _hybrid_recall(
            client,
            query_text="anything",
            project="jarvis",
            mem_type=None,
            limit=5,
        )

        rpc_calls = client.rpc.call_args_list
        assert len(rpc_calls) == 2
        for call in rpc_calls:
            rpc_name, rpc_args = call.args[0], call.args[1]
            assert rpc_args.get("show_history") is False
        rpc_names = {call.args[0] for call in rpc_calls}
        assert "keyword_search_memories" in rpc_names
        assert any(name.startswith("match_memories") for name in rpc_names)

    @pytest.mark.asyncio
    async def test_hybrid_recall_propagates_show_history_true(self, monkeypatch):
        client = MagicMock()
        client.rpc.return_value.execute.return_value = MagicMock(data=[])

        async def _stub_embed(_text):
            return [0.0] * 512

        monkeypatch.setattr(server_module, "_embed_query", _stub_embed)

        await _hybrid_recall(
            client,
            query_text="anything",
            project=None,
            mem_type=None,
            limit=5,
            show_history=True,
        )

        for call in client.rpc.call_args_list:
            assert call.args[1].get("show_history") is True

    @pytest.mark.asyncio
    async def test_keyword_recall_excludes_session_snapshots(self):
        rows = [
            {
                "name": "snap1",
                "type": "project",
                "project": "jarvis",
                "description": "d",
                "content": "c",
                "tags": ["session-snapshot", "auto"],
                "updated_at": "2026-04-25T00:00:00+00:00",
                "valid_to": None,
            },
            {
                "name": "real_mem",
                "type": "decision",
                "project": "jarvis",
                "description": "d",
                "content": "c",
                "tags": ["pillar-4"],
                "updated_at": "2026-04-25T00:00:00+00:00",
                "valid_to": None,
            },
            {
                "name": "snap2",
                "type": "project",
                "project": "jarvis",
                "description": "d",
                "content": "c",
                "tags": ["session-snapshot"],
                "updated_at": "2026-04-25T00:00:00+00:00",
                "valid_to": None,
            },
        ]

        query = MagicMock()
        for method in ("select", "is_", "or_", "eq", "limit", "order"):
            getattr(query, method).return_value = query
        query.execute.return_value = MagicMock(data=rows)
        client = MagicMock()
        client.table.return_value = query

        result = await _keyword_recall(
            client, "anything", project="jarvis", mem_type=None, limit=10
        )
        text = result[0].text if result else ""
        assert "real_mem" in text
        assert "snap1" not in text
        assert "snap2" not in text


# ---------------------------------------------------------------------------
# #286: outcome_record / outcome_update must accept and persist memory_id
# ---------------------------------------------------------------------------


class TestOutcomeMemoryId:
    """Osasuwu/jarvis#286: task_outcomes.memory_id drives memory_calibration."""

    @pytest.fixture(autouse=True)
    def _patch_client(self, monkeypatch):
        self.client = MagicMock()
        self.tbl = MagicMock()
        self.client.table.return_value = self.tbl
        self.tbl.insert.return_value.execute.return_value = MagicMock(data=[{"id": "outcome-uuid"}])
        self.tbl.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "outcome-uuid"}]
        )
        monkeypatch.setattr(server_module, "_get_client", lambda: self.client)

    @pytest.mark.asyncio
    async def test_outcome_record_persists_memory_id(self):
        await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": "11111111-1111-1111-1111-111111111111",
            }
        )

        insert_payload = self.tbl.insert.call_args[0][0]
        assert insert_payload["memory_id"] == "11111111-1111-1111-1111-111111111111"

    @pytest.mark.asyncio
    async def test_outcome_record_omits_memory_id_when_absent(self):
        await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
            }
        )

        insert_payload = self.tbl.insert.call_args[0][0]
        assert "memory_id" not in insert_payload

    @pytest.mark.asyncio
    async def test_outcome_record_ignores_none_memory_id(self):
        await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": None,
            }
        )

        insert_payload = self.tbl.insert.call_args[0][0]
        assert "memory_id" not in insert_payload

    @pytest.mark.asyncio
    async def test_outcome_update_persists_memory_id(self):
        await _handle_outcome_update(
            {
                "id": "outcome-uuid",
                "memory_id": "22222222-2222-2222-2222-222222222222",
            }
        )

        update_payload = self.tbl.update.call_args[0][0]
        assert update_payload["memory_id"] == "22222222-2222-2222-2222-222222222222"

    @pytest.mark.asyncio
    async def test_outcome_update_retrolinks_without_status_change(self):
        result = await _handle_outcome_update(
            {
                "id": "outcome-uuid",
                "memory_id": "33333333-3333-3333-3333-333333333333",
            }
        )

        update_payload = self.tbl.update.call_args[0][0]
        assert update_payload == {"memory_id": "33333333-3333-3333-3333-333333333333"}
        assert "verified_at" not in update_payload
        assert "updated" in result[0].text
