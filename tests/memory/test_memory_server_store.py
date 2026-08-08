"""Unit tests for mcp-memory/server.py — store path: handler, classifier,
auto-links, embedding slots, dual-embed writes, known unknowns.

conftest.py handles the sys.modules stubs for MCP SDK + Supabase before
this file loads, so `from server import` works without the real deps.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from server import (
    _apply_classifier_decision,
    _compute_write_embeddings,
    _create_auto_links,
    _embed_upsert_fields,
    _handle_store,
    _model_slot,
    _resolve_known_unknowns,
    _upsert_known_unknown,
    CLASSIFIER_APPLY_THRESHOLD,
    MAX_AUTO_LINKS,
    SUPERSEDE_SIM_THRESHOLD,
)
from classifier import ClassifierDecision
import server as server_module


# ---------------------------------------------------------------------------
# _create_auto_links (async, mocked Supabase)
# ---------------------------------------------------------------------------


class TestCreateAutoLinks:
    """Auto-linking creates memory_links entries based on similarity.

    Phase 2b changed the contract: links are always created as 'related'
    first, then the classifier (or legacy fallback) decides whether to
    upgrade to 'supersedes' / mark expired.
    """

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        client.table.return_value.select.return_value.in_.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        return client

    def _first_links_upsert(self, mock_client):
        for call in mock_client.table.return_value.upsert.call_args_list:
            arg = call[0][0]
            if isinstance(arg, list):
                return arg
        return []

    @pytest.mark.asyncio
    async def test_creates_related_links(self, mock_client):
        similar = [
            {"id": "target-1", "type": "project", "similarity": 0.70},
            {"id": "target-2", "type": "project", "similarity": 0.65},
        ]
        await _create_auto_links(mock_client, "source-id", similar, mem_type="project")

        links = self._first_links_upsert(mock_client)
        assert len(links) == 2
        assert all(l["link_type"] == "related" for l in links)
        assert links[0]["strength"] == 0.70
        assert links[1]["strength"] == 0.65

    @pytest.mark.asyncio
    async def test_legacy_fallback_supersedes_same_type(self, mock_client, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        similar = [
            {
                "id": "old-decision",
                "type": "decision",
                "similarity": SUPERSEDE_SIM_THRESHOLD + 0.05,
            },
        ]
        await _create_auto_links(mock_client, "new-decision", similar, mem_type="decision")

        update_calls = [
            c
            for c in mock_client.table.return_value.update.call_args_list
            if c[0][0].get("superseded_by") == "new-decision"
        ]
        assert len(update_calls) == 1

    @pytest.mark.asyncio
    async def test_no_supersession_when_below_threshold(self, mock_client, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        similar = [
            {"id": "target", "type": "project", "similarity": 0.70},
        ]
        await _create_auto_links(mock_client, "source", similar, mem_type="project")

        update_calls = [
            c
            for c in mock_client.table.return_value.update.call_args_list
            if c[0][0].get("superseded_by") == "source"
        ]
        assert update_calls == []

    @pytest.mark.asyncio
    async def test_max_links_limit(self, mock_client):
        similar = [{"id": f"t-{i}", "type": "project", "similarity": 0.70} for i in range(10)]
        await _create_auto_links(mock_client, "source", similar, mem_type="project")

        links = self._first_links_upsert(mock_client)
        assert len(links) == MAX_AUTO_LINKS

    @pytest.mark.asyncio
    async def test_empty_similar_rows(self, mock_client):
        await _create_auto_links(mock_client, "source", [], mem_type="project")
        link_calls = [c for c in mock_client.table.call_args_list if c[0][0] == "memory_links"]
        assert link_calls == []

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self, mock_client):
        mock_client.table.side_effect = Exception("DB error")
        await _create_auto_links(
            mock_client, "source", [{"id": "t", "type": "p", "similarity": 0.7}], "project"
        )


# ---------------------------------------------------------------------------
# Phase 2b classifier — _apply_classifier_decision routing + queue writes
# ---------------------------------------------------------------------------


class TestApplyClassifierDecision:
    """Routing of classifier decisions: which DB mutations fire when."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client._tables = {}

        def _get_table(name):
            if name not in client._tables:
                t = MagicMock()
                t.update.return_value.eq.return_value.is_.return_value.execute.return_value = (
                    MagicMock(data=[{"id": "row"}])
                )
                t.insert.return_value.execute.return_value = MagicMock()
                t.upsert.return_value.execute.return_value = MagicMock()
                client._tables[name] = t
            return client._tables[name]

        client.table.side_effect = _get_table
        return client

    def _update_calls(self, mock_client, key: str, table: str = "memories"):
        t = mock_client._tables.get(table)
        if t is None:
            return []
        return [c for c in t.update.call_args_list if isinstance(c[0][0], dict) and key in c[0][0]]

    def _queue_inserts(self, mock_client):
        t = mock_client._tables.get("memory_review_queue")
        if t is None:
            return []
        return t.insert.call_args_list

    def _link_upserts(self, mock_client, link_type: str | None = None):
        t = mock_client._tables.get("memory_links")
        if t is None:
            return []
        calls = t.upsert.call_args_list
        if link_type is None:
            return calls
        out = []
        for c in calls:
            payload = c[0][0]
            if isinstance(payload, dict) and payload.get("link_type") == link_type:
                out.append(c)
        return out

    @pytest.mark.asyncio
    async def test_high_confidence_update_marks_superseded(self, mock_client):
        decision = ClassifierDecision(
            decision="UPDATE",
            target_id="old-id",
            confidence=0.95,
            reasoning="refines target",
        )
        neighbors = [{"id": "old-id", "name": "old", "similarity": 0.82}]
        await _apply_classifier_decision(mock_client, "new-id", decision, neighbors)

        sup_calls = self._update_calls(mock_client, "superseded_by")
        assert len(sup_calls) == 1
        assert sup_calls[0][0][0]["superseded_by"] == "new-id"

        sup_links = self._link_upserts(mock_client, link_type="supersedes")
        assert len(sup_links) == 1
        link_payload = sup_links[0][0][0]
        assert link_payload["source_id"] == "new-id"
        assert link_payload["target_id"] == "old-id"

        inserts = self._queue_inserts(mock_client)
        assert len(inserts) == 1
        payload = inserts[0][0][0]
        assert payload["decision"] == "UPDATE"
        assert payload["status"] == "auto_applied"
        assert payload["target_id"] == "old-id"

    @pytest.mark.asyncio
    async def test_high_confidence_delete_sets_expired(self, mock_client):
        decision = ClassifierDecision(
            decision="DELETE",
            target_id="old-id",
            confidence=0.92,
            reasoning="negates target",
        )
        neighbors = [{"id": "old-id", "name": "old", "similarity": 0.85}]
        await _apply_classifier_decision(mock_client, "new-id", decision, neighbors)

        exp_calls = self._update_calls(mock_client, "expired_at")
        assert len(exp_calls) == 1

        inserts = self._queue_inserts(mock_client)
        assert inserts[0][0][0]["decision"] == "DELETE"
        assert inserts[0][0][0]["status"] == "auto_applied"

    @pytest.mark.asyncio
    async def test_low_confidence_update_queues_pending(self, mock_client):
        decision = ClassifierDecision(
            decision="UPDATE",
            target_id="old-id",
            confidence=CLASSIFIER_APPLY_THRESHOLD - 0.1,
            reasoning="ambiguous",
        )
        neighbors = [{"id": "old-id", "name": "old", "similarity": 0.78}]
        await _apply_classifier_decision(mock_client, "new-id", decision, neighbors)

        sup_calls = self._update_calls(mock_client, "superseded_by")
        assert sup_calls == []

        inserts = self._queue_inserts(mock_client)
        payload = inserts[0][0][0]
        assert payload["status"] == "pending"
        assert payload["applied_at"] is None

    @pytest.mark.asyncio
    async def test_noop_records_decision_no_mutation(self, mock_client):
        decision = ClassifierDecision(
            decision="NOOP",
            target_id=None,
            confidence=0.9,
            reasoning="redundant",
        )
        neighbors = [{"id": "x", "name": "x", "similarity": 0.9}]
        await _apply_classifier_decision(mock_client, "new-id", decision, neighbors)

        assert self._update_calls(mock_client, "superseded_by") == []
        assert self._update_calls(mock_client, "expired_at") == []
        inserts = self._queue_inserts(mock_client)
        assert len(inserts) == 1
        assert inserts[0][0][0]["decision"] == "NOOP"

    @pytest.mark.asyncio
    async def test_high_confidence_add_no_queue_entry(self, mock_client):
        decision = ClassifierDecision(
            decision="ADD",
            target_id=None,
            confidence=0.95,
            reasoning="genuinely new",
        )
        neighbors = [{"id": "x", "name": "x", "similarity": 0.76}]
        await _apply_classifier_decision(mock_client, "new-id", decision, neighbors)

        assert self._queue_inserts(mock_client) == []

    @pytest.mark.asyncio
    async def test_hallucinated_target_id_refused(self, mock_client):
        decision = ClassifierDecision(
            decision="UPDATE",
            target_id="never-existed",
            confidence=0.95,
            reasoning="...",
        )
        neighbors = [{"id": "real-id", "name": "real", "similarity": 0.85}]
        await _apply_classifier_decision(mock_client, "new-id", decision, neighbors)

        assert self._update_calls(mock_client, "superseded_by") == []
        inserts = self._queue_inserts(mock_client)
        payload = inserts[0][0][0]
        assert payload["status"] == "pending"
        assert payload["target_id"] is None


# ---------------------------------------------------------------------------
# Phase 2c: memory_store must reject writes missing source_provenance
# ---------------------------------------------------------------------------


class TestHandleStoreProvenance:
    """Phase 2c — every memory write carries a namespaced source_provenance."""

    @pytest.fixture(autouse=True)
    def _patch_client(self, monkeypatch):
        self.client = MagicMock()
        monkeypatch.setattr(server_module, "_get_client", lambda: self.client)

    @pytest.mark.asyncio
    async def test_rejects_missing_provenance(self):
        result = await _handle_store(
            {
                "type": "project",
                "name": "test_missing",
                "content": "test content",
            }
        )
        assert len(result) == 1
        assert "source_provenance is required" in result[0].text
        self.client.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_blank_provenance(self):
        result = await _handle_store(
            {
                "type": "project",
                "name": "test_blank",
                "content": "test content",
                "source_provenance": "   ",
            }
        )
        assert "source_provenance is required" in result[0].text
        self.client.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_none_provenance(self):
        result = await _handle_store(
            {
                "type": "project",
                "name": "test_none",
                "content": "test content",
                "source_provenance": None,
            }
        )
        assert "source_provenance is required" in result[0].text
        self.client.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_provenance_stripped_before_persist(self, monkeypatch):
        async def _fake_embed(_text, **_kwargs):
            return None

        monkeypatch.setattr(server_module, "_embed", _fake_embed)

        tbl = MagicMock()
        tbl.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "stored-1"}])
        self.client.table.return_value = tbl

        await _handle_store(
            {
                "type": "project",
                "name": "test_strip",
                "content": "test content",
                "project": "jarvis",
                "source_provenance": "  skill:test  ",
            }
        )

        upsert_calls = tbl.upsert.call_args_list
        assert upsert_calls
        data_arg = upsert_calls[-1][0][0]
        assert data_arg["source_provenance"] == "skill:test"


class TestHandleStoreStructuredResponse:
    """#658: success-path returns JSON, not prose."""

    @pytest.fixture(autouse=True)
    def _patch_client(self, monkeypatch):
        self.client = MagicMock()
        monkeypatch.setattr(server_module, "_get_client", lambda: self.client)

        async def _no_embed(_text):
            return {}

        monkeypatch.setattr(server_module, "_compute_write_embeddings", _no_embed)

        async def _noop_links(*_a, **_k):
            return None

        monkeypatch.setattr(server_module, "_create_auto_links", _noop_links)

    @pytest.mark.asyncio
    async def test_project_scoped_upsert_returns_structured_json(self):
        tbl = MagicMock()
        tbl.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "mem-uuid-1"}])
        self.client.table.return_value = tbl

        result = await _handle_store(
            {
                "type": "project",
                "name": "test_struct_project",
                "content": "test content",
                "project": "jarvis",
                "source_provenance": "session:test",
            }
        )

        assert len(result) == 1
        body = json.loads(result[0].text)
        assert body["stored"] is True
        assert body["action"] == "saved"
        assert body["memory_id"] == "mem-uuid-1"
        assert body["project"] == "jarvis"
        assert body["consolidation_candidates"] == []
        assert body["classifier_pending"] is False
        assert "test_struct_project" in body["message"]
        assert "saved" in body["message"]

    @pytest.mark.asyncio
    async def test_global_new_returns_action_created(self):
        tbl = MagicMock()
        select_chain = tbl.select.return_value.eq.return_value.is_.return_value
        select_chain.limit.return_value.execute.return_value = MagicMock(data=[])
        tbl.insert.return_value.execute.return_value = MagicMock(data=[{"id": "mem-new"}])
        self.client.table.return_value = tbl

        result = await _handle_store(
            {
                "type": "feedback",
                "name": "test_struct_global_new",
                "content": "x",
                "source_provenance": "session:test",
            }
        )

        body = json.loads(result[0].text)
        assert body["stored"] is True
        assert body["action"] == "created"
        assert body["memory_id"] == "mem-new"
        assert body["project"] == "global"

    @pytest.mark.asyncio
    async def test_global_existing_returns_action_updated(self):
        tbl = MagicMock()
        select_chain = tbl.select.return_value.eq.return_value.is_.return_value
        select_chain.limit.return_value.execute.return_value = MagicMock(
            data=[{"id": "mem-existing"}]
        )
        tbl.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        self.client.table.return_value = tbl

        result = await _handle_store(
            {
                "type": "feedback",
                "name": "test_struct_global_update",
                "content": "x",
                "source_provenance": "session:test",
            }
        )

        body = json.loads(result[0].text)
        assert body["stored"] is True
        assert body["action"] == "updated"
        assert body["memory_id"] == "mem-existing"
        assert body["project"] == "global"

    @pytest.mark.asyncio
    async def test_consolidation_uses_neutral_phrasing(self, monkeypatch):
        async def _real_embed(_text):
            return {"embedding": [0.1] * 512}

        monkeypatch.setattr(server_module, "_compute_write_embeddings", _real_embed)

        tbl = MagicMock()
        tbl.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "mem-uuid-2"}])
        self.client.table.return_value = tbl

        self.client.rpc.return_value.execute.return_value = MagicMock(
            data=[
                {"id": "sib-1", "name": "sibling_a", "similarity": 0.85},
                {"id": "sib-2", "name": "sibling_b", "similarity": 0.82},
                {"id": "sib-3", "name": "sibling_c", "similarity": 0.81},
            ]
        )

        result = await _handle_store(
            {
                "type": "feedback",
                "name": "test_struct_consolidation",
                "content": "content",
                "project": "jarvis",
                "source_provenance": "session:test",
            }
        )

        body = json.loads(result[0].text)
        assert body["stored"] is True
        assert body["action"] == "saved"
        assert "⚠" not in body["message"]
        assert "hint" not in body["message"].lower()
        assert "info:" in body["message"].lower()
        assert body["consolidation_candidates"] == [
            "sibling_a",
            "sibling_b",
            "sibling_c",
        ]
        assert body["classifier_pending"] is True


# ---------------------------------------------------------------------------
# #242: dual-embedding machinery — column/RPC mapping + dual-write
# ---------------------------------------------------------------------------


class TestModelSlotMapping:
    """#242: the model -> column/RPC table drives both read and write paths."""

    def test_voyage_3_lite_maps_to_v1_column(self):
        slot = _model_slot("voyage-3-lite")
        assert slot["embedding_column"] == "embedding"
        assert slot["rpc"] == "match_memories"

    def test_voyage_3_maps_to_v2_column(self):
        slot = _model_slot("voyage-3")
        assert slot["embedding_column"] == "embedding_v2"
        assert slot["rpc"] == "match_memories_v2"

    def test_unknown_model_falls_back_to_legacy(self):
        slot = _model_slot("nonexistent-model")
        assert slot["embedding_column"] == "embedding"
        assert slot["rpc"] == "match_memories"

    def test_upsert_fields_shape(self):
        fields = _embed_upsert_fields([0.1, 0.2], "voyage-3-lite")
        assert fields == {
            "embedding": [0.1, 0.2],
            "embedding_model": "voyage-3-lite",
            "embedding_version": "v2",
        }

    def test_upsert_fields_v2(self):
        fields = _embed_upsert_fields([0.3, 0.4], "voyage-3")
        assert fields == {
            "embedding_v2": [0.3, 0.4],
            "embedding_model_v2": "voyage-3",
            "embedding_version_v2": "v2",
        }

    def test_upsert_fields_unknown_returns_empty(self):
        assert _embed_upsert_fields([0.1], "no-such-model") == {}


class TestDualEmbedWrite:
    """#242: when SECONDARY is set, writes compute both embeddings."""

    @pytest.mark.asyncio
    async def test_secondary_unset_single_write(self, monkeypatch):
        calls: list[dict] = []

        async def fake_embed(text, input_type="document", model=None):
            calls.append({"model": model})
            return [0.1, 0.2, 0.3]

        monkeypatch.setattr(server_module, "_embed", fake_embed)
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_PRIMARY", "voyage-3-lite")
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_SECONDARY", None)

        fields = await _compute_write_embeddings("canonical text")

        assert "embedding" in fields
        assert "embedding_model" in fields
        assert fields["embedding_model"] == "voyage-3-lite"
        assert "embedding_v2" not in fields
        assert len(calls) == 1
        assert calls[0]["model"] == "voyage-3-lite"

    @pytest.mark.asyncio
    async def test_secondary_set_dual_write(self, monkeypatch):
        calls: list[dict] = []

        async def fake_embed(text, input_type="document", model=None):
            calls.append({"model": model})
            return [0.1] * 512 if model == "voyage-3-lite" else [0.9] * 1024

        monkeypatch.setattr(server_module, "_embed", fake_embed)
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_PRIMARY", "voyage-3-lite")
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_SECONDARY", "voyage-3")

        fields = await _compute_write_embeddings("canonical text")

        assert fields["embedding"] == [0.1] * 512
        assert fields["embedding_v2"] == [0.9] * 1024
        assert fields["embedding_model"] == "voyage-3-lite"
        assert fields["embedding_model_v2"] == "voyage-3"
        assert {c["model"] for c in calls} == {"voyage-3-lite", "voyage-3"}

    @pytest.mark.asyncio
    async def test_secondary_failure_single_leg(self, monkeypatch):
        async def fake_embed(text, input_type="document", model=None):
            if model == "voyage-3":
                return None
            return [0.1] * 512

        monkeypatch.setattr(server_module, "_embed", fake_embed)
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_PRIMARY", "voyage-3-lite")
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_SECONDARY", "voyage-3")

        fields = await _compute_write_embeddings("canonical text")
        assert fields["embedding"] == [0.1] * 512
        assert "embedding_v2" not in fields

    @pytest.mark.asyncio
    async def test_primary_failure_no_write(self, monkeypatch):
        async def fake_embed(text, input_type="document", model=None):
            return None

        monkeypatch.setattr(server_module, "_embed", fake_embed)
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_PRIMARY", "voyage-3-lite")
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_SECONDARY", "voyage-3")

        fields = await _compute_write_embeddings("canonical text")
        assert fields == {}

    @pytest.mark.asyncio
    async def test_secondary_equals_primary_no_duplicate_call(self, monkeypatch):
        calls: list[dict] = []

        async def fake_embed(text, input_type="document", model=None):
            calls.append({"model": model})
            return [0.1] * 512

        monkeypatch.setattr(server_module, "_embed", fake_embed)
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_PRIMARY", "voyage-3-lite")
        monkeypatch.setattr(server_module, "EMBEDDING_MODEL_SECONDARY", "voyage-3-lite")

        fields = await _compute_write_embeddings("canonical text")
        assert len(calls) == 1
        assert "embedding" in fields
        assert "embedding_v2" not in fields


# =========================================================================
# Known unknowns — retrieval gaps + unsatisfied queries (#249)
# =========================================================================


class TestKnownUnknowns:
    """Unit tests for known_unknowns insertion + dedup + resolution."""

    @pytest.mark.asyncio
    async def test_known_unknowns_insert_on_low_sim(self):
        mock_client = MagicMock()

        mock_select_chain = MagicMock()
        mock_select_chain.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[]
        )

        mock_insert = MagicMock()
        mock_update = MagicMock()

        mock_table = MagicMock()
        mock_table.select.return_value = mock_select_chain
        mock_table.insert.return_value = mock_insert
        mock_table.update.return_value = mock_update
        mock_client.table.return_value = mock_table

        await _upsert_known_unknown(
            mock_client,
            query="what is the meaning of life",
            query_embedding=[0.1, 0.2, 0.3],
            top_similarity=0.3,
            top_memory_id="mem-123",
            context={"project": "jarvis"},
        )

        mock_insert.execute.assert_called_once()
        insert_payload = mock_table.insert.call_args.args[0]
        assert insert_payload["query"] == "what is the meaning of life"
        assert insert_payload["top_similarity"] == 0.3
        assert insert_payload["top_memory_id"] == "mem-123"
        assert insert_payload["query_embedding"] is None
        assert not mock_update.execute.called

    @pytest.mark.asyncio
    async def test_known_unknowns_dedup_increments_hit_count(self):
        mock_client = MagicMock()

        existing_embedding = [0.10] * 512
        similar_embedding = [0.11] * 512

        mock_select_return = MagicMock()
        mock_select_return.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "uk-1", "query_embedding": existing_embedding, "hit_count": 5}]
        )

        mock_update_return = MagicMock()
        mock_insert_return = MagicMock()

        mock_table = MagicMock()
        mock_table.select.return_value = mock_select_return
        mock_table.update.return_value = mock_update_return
        mock_table.insert.return_value = mock_insert_return
        mock_client.table.return_value = mock_table

        await _upsert_known_unknown(
            mock_client,
            query="what is the meaning of existence",
            query_embedding=similar_embedding,
            top_similarity=0.35,
            top_memory_id="mem-456",
        )

        select_cols = mock_table.select.call_args.args[0]
        assert "hit_count" in select_cols

        mock_table.update.assert_called_once()
        update_payload = mock_table.update.call_args.args[0]
        assert update_payload["hit_count"] == 6

        assert not mock_insert_return.execute.called

    @pytest.mark.asyncio
    async def test_known_unknowns_resolution_on_store(self):
        mock_client = MagicMock()
        unknown_embedding = [0.5, 0.5, 0.0]

        mock_select = MagicMock()
        mock_eq = MagicMock()
        mock_eq.execute.return_value = MagicMock(
            data=[{"id": "uk-2", "query_embedding": unknown_embedding}]
        )
        mock_select.eq.return_value = mock_eq

        mock_update = MagicMock()
        mock_update_eq = MagicMock()
        mock_update_eq.execute.return_value = MagicMock()
        mock_update.eq.return_value = mock_update_eq

        def table_side_effect(table_name):
            if table_name == "known_unknowns":
                result = MagicMock()
                result.select.return_value = mock_select
                result.update.return_value = mock_update
                return result
            return MagicMock()

        mock_client.table.side_effect = table_side_effect

        memory_embedding = [0.6, 0.55, 0.1]
        await _resolve_known_unknowns(mock_client, memory_embedding, "mem-789")

        assert mock_update.eq.called
