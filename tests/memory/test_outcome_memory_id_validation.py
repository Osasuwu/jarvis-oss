"""Osasuwu/jarvis#660: outcome_record/outcome_update must reject a
decision-episode UUID passed as memory_id — that FK only accepts memories.id,
and record_decision returns an episodes.id. Covers the shared pre-flight
validation helper wired into both handlers.
"""

from __future__ import annotations

import pytest

from server import _handle_outcome_record, _handle_outcome_update
import server as server_module

from supabase_stubs import FakeClient

MEMORY_UUID = "11111111-1111-1111-1111-111111111111"
EPISODE_UUID = "22222222-2222-2222-2222-222222222222"
GARBAGE_UUID = "33333333-3333-3333-3333-333333333333"


def _client_with(*, memory_ids=(), episode_ids=()):
    """FakeClient wired so table("memories")/table("episodes") id lookups
    hit only for the given ids; task_outcomes insert/update always succeeds.
    """
    client = FakeClient()

    def _memories_handler(call):
        if call["op"] != "select":
            return []
        wanted = None
        for f in call["filters"]:
            if f[0] == "eq" and f[1] == "id":
                wanted = f[2]
        return [{"id": wanted}] if wanted in memory_ids else []

    def _episodes_handler(call):
        if call["op"] != "select":
            return []
        wanted = None
        for f in call["filters"]:
            if f[0] == "eq" and f[1] == "id":
                wanted = f[2]
        return [{"id": wanted}] if wanted in episode_ids else []

    def _task_outcomes_handler(call):
        if call["op"] == "insert":
            return [{"id": "outcome-uuid"}]
        if call["op"] == "update":
            return [{"id": "outcome-uuid"}]
        return []

    client.table_handlers["memories"] = _memories_handler
    client.table_handlers["episodes"] = _episodes_handler
    client.table_handlers["task_outcomes"] = _task_outcomes_handler
    return client


class TestOutcomeRecordMemoryIdValidation:
    @pytest.mark.asyncio
    async def test_non_uuid_string_rejected_without_sql(self, monkeypatch):
        client = _client_with()
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": "not-a-uuid",
            }
        )

        assert "not a valid UUID" in result[0].text
        assert client.table_calls == []

    @pytest.mark.asyncio
    async def test_episode_uuid_rejected_with_specific_message(self, monkeypatch):
        client = _client_with(episode_ids={EPISODE_UUID})
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": EPISODE_UUID,
            }
        )

        assert "episode" in result[0].text.lower()
        assert "memories_used[0]" in result[0].text
        insert_calls = [c for c in client.table_calls if c["table"] == "task_outcomes"]
        assert insert_calls == []

    @pytest.mark.asyncio
    async def test_garbage_uuid_not_found_in_either_table(self, monkeypatch):
        client = _client_with()
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": GARBAGE_UUID,
            }
        )

        assert "not found" in result[0].text.lower()
        assert "memories" in result[0].text and "episodes" in result[0].text
        insert_calls = [c for c in client.table_calls if c["table"] == "task_outcomes"]
        assert insert_calls == []

    @pytest.mark.asyncio
    async def test_valid_memory_id_still_inserts(self, monkeypatch):
        client = _client_with(memory_ids={MEMORY_UUID})
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": MEMORY_UUID,
            }
        )

        assert "Outcome recorded" in result[0].text
        insert_calls = [
            c for c in client.table_calls if c["table"] == "task_outcomes" and c["op"] == "insert"
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0]["row"]["memory_id"] == MEMORY_UUID

    @pytest.mark.asyncio
    async def test_absent_memory_id_is_noop_passthrough(self, monkeypatch):
        client = _client_with()
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
            }
        )

        assert "Outcome recorded" in result[0].text
        memories_calls = [c for c in client.table_calls if c["table"] in ("memories", "episodes")]
        assert memories_calls == []


class TestOutcomeUpdateMemoryIdValidation:
    @pytest.mark.asyncio
    async def test_non_uuid_string_rejected_without_sql(self, monkeypatch):
        client = _client_with()
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_update({"id": "outcome-uuid", "memory_id": "not-a-uuid"})

        assert "not a valid UUID" in result[0].text
        assert client.table_calls == []

    @pytest.mark.asyncio
    async def test_episode_uuid_rejected_with_specific_message(self, monkeypatch):
        client = _client_with(episode_ids={EPISODE_UUID})
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_update({"id": "outcome-uuid", "memory_id": EPISODE_UUID})

        assert "episode" in result[0].text.lower()
        assert "memories_used[0]" in result[0].text
        update_calls = [c for c in client.table_calls if c["table"] == "task_outcomes"]
        assert update_calls == []

    @pytest.mark.asyncio
    async def test_garbage_uuid_not_found_in_either_table(self, monkeypatch):
        client = _client_with()
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_update({"id": "outcome-uuid", "memory_id": GARBAGE_UUID})

        assert "not found" in result[0].text.lower()
        assert "memories" in result[0].text and "episodes" in result[0].text
        update_calls = [c for c in client.table_calls if c["table"] == "task_outcomes"]
        assert update_calls == []

    @pytest.mark.asyncio
    async def test_valid_memory_id_still_updates(self, monkeypatch):
        client = _client_with(memory_ids={MEMORY_UUID})
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_update({"id": "outcome-uuid", "memory_id": MEMORY_UUID})

        assert "updated" in result[0].text
        update_calls = [
            c for c in client.table_calls if c["table"] == "task_outcomes" and c["op"] == "update"
        ]
        assert len(update_calls) == 1
        assert update_calls[0]["row"]["memory_id"] == MEMORY_UUID

    @pytest.mark.asyncio
    async def test_absent_memory_id_is_noop_passthrough(self, monkeypatch):
        client = _client_with()
        monkeypatch.setattr(server_module, "_get_client", lambda: client)

        result = await _handle_outcome_update({"id": "outcome-uuid", "outcome_status": "success"})

        assert "updated" in result[0].text
        memories_calls = [c for c in client.table_calls if c["table"] in ("memories", "episodes")]
        assert memories_calls == []


class TestRecordDecisionEpisodeConfusionReproducer:
    """Osasuwu/jarvis#660 reproducer: record_decision's returned episode UUID
    must never silently pass through outcome_record — it must produce the
    specific episode-confusion TextContent, not a generic error, and must
    not write a task_outcomes row.
    """

    @pytest.mark.asyncio
    async def test_record_decision_episode_uuid_rejected_by_outcome_record(self, monkeypatch):
        from server import _handle_record_decision

        decision_client = FakeClient()
        decision_client.table_handlers["episodes"] = lambda call: (
            [{"id": EPISODE_UUID}] if call["op"] == "insert" else []
        )
        monkeypatch.setattr(server_module, "_get_client", lambda: decision_client)

        decision_result = await _handle_record_decision(
            {
                "decision": "test decision",
                "rationale": "test rationale",
                "alternatives_considered": [],
                "reversibility": "reversible",
                "confidence": 0.9,
                "memories_used": [],
                "actor": "session:test",
                "project": "jarvis",
            }
        )
        assert EPISODE_UUID in decision_result[0].text

        outcome_client = _client_with(episode_ids={EPISODE_UUID})
        monkeypatch.setattr(server_module, "_get_client", lambda: outcome_client)

        outcome_result = await _handle_outcome_record(
            {
                "task_type": "delegation",
                "task_description": "test",
                "outcome_status": "success",
                "memory_id": EPISODE_UUID,
            }
        )

        assert "Error:" not in outcome_result[0].text
        assert "episode" in outcome_result[0].text.lower()
        insert_calls = [
            c
            for c in outcome_client.table_calls
            if c["table"] == "task_outcomes" and c["op"] == "insert"
        ]
        assert insert_calls == []
