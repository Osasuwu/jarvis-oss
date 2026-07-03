"""Unit tests for scripts/consolidation-review.py — evolution + consolidation paths.

Covers the 5.2-γ (#235) CLI extension: list with --kind filter, evolution
diff rendering, approve path (RPC + snapshot reconciliation + audit delete),
and reject path (Python-side status flip). The consolidation paths get a
light regression check so the shared dispatcher (approve/reject/show_diff)
doesn't drift.

Network + DB are stubbed via FakeClient. Haiku/VoyageAI HTTP paths are
untouched by this change so they're not re-tested.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from test_utils import FakeClient, FakeResp, filter_val


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "consolidation-review.py"

# Hyphen in filename → import via spec_from_file_location.
spec = importlib.util.spec_from_file_location("consolidation_review", SCRIPT_PATH)
assert spec and spec.loader
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


# ---------------------------------------------------------------------------
# _kind_for_decision / _decisions_for_kind
# ---------------------------------------------------------------------------


class TestKindRouting:
    def test_evolve_is_evolution(self):
        assert review._kind_for_decision("EVOLVE") == "evolution"

    def test_merge_is_consolidation(self):
        assert review._kind_for_decision("MERGE") == "consolidation"

    def test_supersede_is_consolidation(self):
        assert review._kind_for_decision("SUPERSEDE_CONSOLIDATION") == "consolidation"

    def test_unknown_is_unknown(self):
        assert review._kind_for_decision("WAT") == "unknown"

    def test_decisions_for_all(self):
        out = review._decisions_for_kind("all")
        assert set(out) == {"MERGE", "SUPERSEDE_CONSOLIDATION", "EVOLVE"}

    def test_decisions_for_evolution(self):
        assert review._decisions_for_kind("evolution") == ["EVOLVE"]

    def test_decisions_for_consolidation(self):
        assert set(review._decisions_for_kind("consolidation")) == {
            "MERGE",
            "SUPERSEDE_CONSOLIDATION",
        }


# ---------------------------------------------------------------------------
# list_pending — --kind filter threading
# ---------------------------------------------------------------------------


class TestListPending:
    def test_passes_kind_decisions_to_query(self):
        client = FakeClient()
        client.table_handlers["memory_review_queue"] = lambda call: []
        review.list_pending(client, limit=5, kind="evolution")
        call = client.table_calls[-1]
        in_filter = next(f for f in call["filters"] if f[0] == "in")
        assert in_filter[1] == "decision"
        assert in_filter[2] == ["EVOLVE"]

    def test_kind_all_covers_both(self):
        client = FakeClient()
        client.table_handlers["memory_review_queue"] = lambda call: []
        review.list_pending(client, limit=5, kind="all")
        call = client.table_calls[-1]
        in_filter = next(f for f in call["filters"] if f[0] == "in")
        assert set(in_filter[2]) == {"MERGE", "SUPERSEDE_CONSOLIDATION", "EVOLVE"}

    def test_kind_consolidation_excludes_evolve(self):
        client = FakeClient()
        client.table_handlers["memory_review_queue"] = lambda call: []
        review.list_pending(client, limit=5, kind="consolidation")
        call = client.table_calls[-1]
        in_filter = next(f for f in call["filters"] if f[0] == "in")
        assert "EVOLVE" not in in_filter[2]


# ---------------------------------------------------------------------------
# _subjects_for_row — one-line rendering hint
# ---------------------------------------------------------------------------


class TestSubjectsForRow:
    def test_consolidation_row_lists_member_names(self):
        row = {
            "decision": "MERGE",
            "consolidation_payload": {
                "member_names": ["foo", "bar", "baz", "qux"],
            },
        }
        s = review._subjects_for_row(row)
        assert "foo" in s and "bar" in s and "baz" in s
        # 4 members, only 3 shown → "+1" suffix
        assert "+1" in s

    def test_evolution_row_counts_actionable(self):
        row = {
            "decision": "EVOLVE",
            "evolution_payload": {
                "proposals": [
                    {"neighbor_id": "aaaaaaaa-0000", "action": "KEEP"},
                    {"neighbor_id": "bbbbbbbb-1111", "action": "UPDATE_TAGS"},
                    {"neighbor_id": "cccccccc-2222", "action": "UPDATE_DESC"},
                ]
            },
        }
        s = review._subjects_for_row(row)
        assert "2/3 actionable" in s
        assert "bbbbbbbb" in s
        assert "cccccccc" in s

    def test_evolution_row_all_keep(self):
        row = {
            "decision": "EVOLVE",
            "evolution_payload": {
                "proposals": [{"neighbor_id": "n1", "action": "KEEP"}],
            },
        }
        s = review._subjects_for_row(row)
        assert "0/1 actionable" in s


# ---------------------------------------------------------------------------
# render_evolution_diff
# ---------------------------------------------------------------------------


class TestRenderEvolutionDiff:
    def _row(self, proposals: list[dict], *, snapshots: list | None = None) -> dict:
        payload = {
            "update_queue_id": "uq-1",
            "candidate_id": "cand-1",
            "target_id": "tgt-1",
            "proposals": proposals,
        }
        if snapshots is not None:
            payload["snapshots"] = snapshots
        return {
            "id": "queue-1",
            "decision": "EVOLVE",
            "confidence": 0.82,
            "classifier_model": "claude-haiku-4-5",
            "reasoning": "neighbor desc drifted",
            "evolution_payload": payload,
        }

    def test_header_shows_decision_and_ids(self):
        row = self._row([{"neighbor_id": "n1", "action": "KEEP"}])
        out = review.render_evolution_diff(row, {})
        assert "Decision: EVOLVE" in out
        assert "confidence=0.82" in out
        assert "claude-haiku-4-5" in out
        assert "neighbor desc drifted" in out
        assert "uq-1" in out
        assert "cand-1" in out
        assert "tgt-1" in out

    def test_all_keep_shows_no_actionable_note(self):
        row = self._row(
            [
                {"neighbor_id": "n1", "action": "KEEP", "confidence": 0.9},
            ]
        )
        out = review.render_evolution_diff(row, {"n1": {"name": "n1_name"}})
        assert "No actionable proposals" in out

    def test_actionable_renders_old_and_new(self):
        row = self._row(
            [
                {
                    "neighbor_id": "n1",
                    "action": "UPDATE_TAGS",
                    "new_tags": ["fresh", "v2"],
                    "confidence": 0.9,
                    "reasoning": "old tag naming deprecated version",
                },
                {
                    "neighbor_id": "n2",
                    "action": "UPDATE_BOTH",
                    "new_tags": ["t"],
                    "new_description": "revised description",
                    "confidence": 0.7,
                },
            ]
        )
        neighbors = {
            "n1": {"name": "n1_name", "tags": ["old", "deprecated"], "description": "d1"},
            "n2": {"name": "n2_name", "tags": [], "description": "original d2"},
        }
        out = review.render_evolution_diff(row, neighbors)
        assert "UPDATE_TAGS" in out and "UPDATE_BOTH" in out
        assert "n1_name" in out and "n2_name" in out
        # Old → new on n1 tags
        assert "old, deprecated" in out
        assert "fresh, v2" in out
        # Description diff on n2
        assert "original d2" in out
        assert "revised description" in out

    def test_unknown_neighbor_falls_back_to_id(self):
        row = self._row(
            [
                {
                    "neighbor_id": "missing-id",
                    "action": "UPDATE_TAGS",
                    "new_tags": ["x"],
                    "confidence": 0.9,
                },
            ]
        )
        out = review.render_evolution_diff(row, {})
        assert "(current neighbor state unavailable" in out

    def test_snapshots_note_when_present(self):
        row = self._row(
            [
                {
                    "neighbor_id": "n1",
                    "action": "UPDATE_TAGS",
                    "new_tags": ["x"],
                    "confidence": 0.9,
                }
            ],
            snapshots=[{"neighbor_id": "n1", "action": "UPDATE_TAGS"}],
        )
        out = review.render_evolution_diff(row, {"n1": {"name": "n1"}})
        assert "1 apply snapshot" in out


# ---------------------------------------------------------------------------
# _approve_evolution_row — full happy path
# ---------------------------------------------------------------------------


class TestApproveEvolutionRow:
    def _row(self) -> dict:
        return {
            "id": "queue-orig",
            "decision": "EVOLVE",
            "status": "pending",
            "confidence": 0.7,
            "reasoning": "keep this reasoning",
            "classifier_model": "claude-haiku-4-5",
            "evolution_payload": {
                "update_queue_id": "uq-1",
                "candidate_id": "cand-1",
                "target_id": "tgt-1",
                "proposals": [
                    {
                        "neighbor_id": "n1",
                        "action": "UPDATE_TAGS",
                        "new_tags": ["fresh"],
                        "new_description": None,
                        "confidence": 0.7,
                        "reasoning": "stale tag",
                    },
                    {
                        "neighbor_id": "n2",
                        "action": "KEEP",
                        "confidence": 0.9,
                    },
                ],
            },
        }

    def _build_client(self, *, snapshots: list | None = None) -> FakeClient:
        client = FakeClient()

        if snapshots is None:
            snapshots = [
                {
                    "neighbor_id": "n1",
                    "action": "UPDATE_TAGS",
                    "old_tags": ["old1"],
                    "old_description": "d",
                    "new_tags": ["fresh"],
                    "new_description": None,
                }
            ]

        client.rpc_handlers["apply_evolution_plan"] = lambda params: {
            "status": "applied",
            "decision": "EVOLVE",
            "applied_count": 1,
            "queue_id": "audit-queue-new",
        }

        def table_handler(call):
            t = call["table"]
            if t == "memory_review_queue":
                if call["op"] == "select":
                    # Only audit-row fetch uses .eq("id", audit-queue-new)
                    if ("eq", "id", "audit-queue-new") in call["filters"]:
                        return [
                            {
                                "evolution_payload": {
                                    "snapshots": snapshots,
                                }
                            }
                        ]
                    return []
                # update / delete on memory_review_queue → just echo back
                return [{"id": filter_val(call, "eq", "id")}]
            if t == "events":
                return [{"id": "event-fake"}]
            return []

        client.table_handlers["memory_review_queue"] = table_handler
        client.table_handlers["events"] = table_handler
        return client

    def test_rpc_called_with_filtered_actionable_plan(self, capsys):
        client = self._build_client()
        code = review._approve_evolution_row(client, self._row(), as_json=True)
        assert code == 0

        rpc = client.rpc_calls[0]
        assert rpc["name"] == "apply_evolution_plan"
        plan = rpc["params"]["plan"]
        assert plan["decision"] == "EVOLVE"
        assert plan["candidate_id"] == "cand-1"
        assert plan["target_id"] == "tgt-1"
        assert plan["source_provenance"].startswith("cli:review:approve:")
        # KEEP proposal filtered out, only UPDATE_TAGS remains
        assert len(plan["proposals"]) == 1
        assert plan["proposals"][0]["neighbor_id"] == "n1"

    def test_queue_meta_carries_status_approved(self):
        client = self._build_client()
        review._approve_evolution_row(client, self._row(), as_json=True)
        rpc = client.rpc_calls[0]
        meta = rpc["params"]["queue_meta"]
        assert meta["decision"] == "EVOLVE"
        assert meta["status"] == "approved"
        assert meta["classifier_model"] == "claude-haiku-4-5"

    def test_original_row_updated_with_snapshots_and_approved_status(self):
        client = self._build_client()
        review._approve_evolution_row(client, self._row(), as_json=True)
        # Find the update-call on the original pending row
        updates = [
            c
            for c in client.table_calls
            if c["table"] == "memory_review_queue"
            and c["op"] == "update"
            and ("eq", "id", "queue-orig") in c["filters"]
        ]
        assert len(updates) == 1
        row = updates[0]["row"]
        assert row["status"] == "approved"
        assert row["reviewed_by"] == "cli_review"
        assert row["evolution_payload"]["snapshots"]
        assert row["evolution_payload"]["snapshots"][0]["neighbor_id"] == "n1"
        assert row["evolution_payload"]["source_provenance"].startswith("cli:review:approve:")

    def test_audit_duplicate_deleted(self):
        client = self._build_client()
        review._approve_evolution_row(client, self._row(), as_json=True)
        deletes = [
            c
            for c in client.table_calls
            if c["table"] == "memory_review_queue"
            and c["op"] == "delete"
            and ("eq", "id", "audit-queue-new") in c["filters"]
        ]
        assert len(deletes) == 1

    def test_event_written_on_approve(self):
        client = self._build_client()
        review._approve_evolution_row(client, self._row(), as_json=True)
        events = [c for c in client.table_calls if c["table"] == "events"]
        assert len(events) == 1
        ev = events[0]["row"]
        assert ev["event_type"] == "evolution_applied"
        assert ev["payload"]["queue_id"] == "queue-orig"
        assert ev["payload"]["applied_count"] == 1

    def test_json_output_shape(self, capsys):
        client = self._build_client()
        code = review._approve_evolution_row(client, self._row(), as_json=True)
        assert code == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["status"] == "approved"
        assert parsed["decision"] == "EVOLVE"
        assert parsed["queue_id"] == "queue-orig"
        assert parsed["applied_count"] == 1
        assert parsed["snapshot_count"] == 1
        assert parsed["event_id"] == "event-fake"

    def test_no_actionable_proposals_short_circuits(self, capsys):
        client = self._build_client()
        row = self._row()
        row["evolution_payload"]["proposals"] = [
            {"neighbor_id": "n1", "action": "KEEP", "confidence": 0.99},
        ]
        code = review._approve_evolution_row(client, row, as_json=True)
        assert code == 1
        # No RPC should have been called
        assert not client.rpc_calls

    def test_missing_candidate_or_target_fails_fast(self):
        client = self._build_client()
        row = self._row()
        row["evolution_payload"]["candidate_id"] = None
        code = review._approve_evolution_row(client, row, as_json=True)
        assert code == 1
        assert not client.rpc_calls


# ---------------------------------------------------------------------------
# _reject_evolution_row — pure status flip
# ---------------------------------------------------------------------------


class TestRejectEvolutionRow:
    def _row(self) -> dict:
        return {
            "id": "queue-evolve-1",
            "decision": "EVOLVE",
            "status": "pending",
            "confidence": 0.6,
            "reasoning": "original haiku reasoning",
        }

    def _build_client(self) -> FakeClient:
        client = FakeClient()

        def handler(call):
            if call["table"] == "events":
                return [{"id": "event-reject"}]
            # memory_review_queue update → echo
            if call["op"] == "update":
                return [{"id": filter_val(call, "eq", "id")}]
            return []

        client.table_handlers["memory_review_queue"] = handler
        client.table_handlers["events"] = handler
        return client

    def test_update_flips_status_and_records_reviewer(self):
        client = self._build_client()
        code = review._reject_evolution_row(client, self._row(), reason="off-topic", as_json=False)
        assert code == 0
        updates = [c for c in client.table_calls if c["op"] == "update"]
        assert len(updates) == 1
        row = updates[0]["row"]
        assert row["status"] == "rejected"
        assert row["reviewed_by"] == "cli_review"
        assert "off-topic" in row["reasoning"]
        assert "original haiku reasoning" in row["reasoning"]

    def test_no_reason_keeps_original_reasoning(self):
        client = self._build_client()
        review._reject_evolution_row(client, self._row(), reason=None, as_json=False)
        updates = [c for c in client.table_calls if c["op"] == "update"]
        assert updates[0]["row"]["reasoning"] == "original haiku reasoning"

    def test_empty_reason_does_not_append(self):
        client = self._build_client()
        review._reject_evolution_row(client, self._row(), reason="   ", as_json=False)
        updates = [c for c in client.table_calls if c["op"] == "update"]
        assert "rejected:" not in updates[0]["row"]["reasoning"]

    def test_event_written(self):
        client = self._build_client()
        review._reject_evolution_row(client, self._row(), reason="bad plan", as_json=False)
        events = [c for c in client.table_calls if c["table"] == "events"]
        assert len(events) == 1
        ev = events[0]["row"]
        assert ev["event_type"] == "evolution_rejected"
        assert ev["payload"]["decision"] == "EVOLVE"
        assert ev["payload"]["reason"] == "bad plan"

    def test_no_consolidation_rpc_called(self):
        client = self._build_client()
        review._reject_evolution_row(client, self._row(), reason=None, as_json=False)
        # EVOLVE reject must NOT call reject_consolidation RPC
        assert not client.rpc_calls


# ---------------------------------------------------------------------------
# Dispatcher routing — approve / reject / show_diff
# ---------------------------------------------------------------------------


class TestDispatcherRouting:
    def _pending_row(self, decision: str, **extra) -> dict:
        base = {
            "id": "q-1",
            "decision": decision,
            "status": "pending",
            "confidence": 0.8,
            "reasoning": "r",
            "classifier_model": "m",
            "consolidation_payload": None,
            "evolution_payload": None,
            "target_id": None,
            "created_at": "2026-04-20T00:00:00+00:00",
            "reviewed_at": None,
            "reviewed_by": None,
            "applied_at": None,
        }
        base.update(extra)
        return base

    def _client_returning(self, row) -> FakeClient:
        client = FakeClient()

        def handler(call):
            if call["op"] == "select" and ("eq", "id", row["id"]) in call["filters"]:
                return [row]
            if call["op"] == "select" and call["table"] == "memories":
                return []
            if call["table"] == "events":
                return [{"id": "e"}]
            return [{"id": row["id"]}]

        client.table_handlers["memory_review_queue"] = handler
        client.table_handlers["events"] = handler
        client.table_handlers["memories"] = handler
        return client

    def test_approve_routes_evolve_to_evolution_path(self, monkeypatch):
        row = self._pending_row(
            "EVOLVE",
            evolution_payload={
                "update_queue_id": "u",
                "candidate_id": "c",
                "target_id": "t",
                "proposals": [
                    {
                        "neighbor_id": "n",
                        "action": "UPDATE_TAGS",
                        "new_tags": ["x"],
                        "confidence": 0.9,
                    }
                ],
            },
        )
        client = self._client_returning(row)
        called = {"consolidation": False, "evolution": False}

        monkeypatch.setattr(
            review,
            "_approve_consolidation_row",
            lambda *a, **k: called.__setitem__("consolidation", True) or 0,
        )
        monkeypatch.setattr(
            review,
            "_approve_evolution_row",
            lambda *a, **k: called.__setitem__("evolution", True) or 0,
        )
        code = review.approve(client, "q-1", as_json=True)
        assert code == 0
        assert called == {"consolidation": False, "evolution": True}

    def test_approve_routes_merge_to_consolidation_path(self, monkeypatch):
        row = self._pending_row("MERGE", consolidation_payload={"member_ids": []})
        client = self._client_returning(row)
        called = {"consolidation": False, "evolution": False}
        monkeypatch.setattr(
            review,
            "_approve_consolidation_row",
            lambda *a, **k: called.__setitem__("consolidation", True) or 0,
        )
        monkeypatch.setattr(
            review,
            "_approve_evolution_row",
            lambda *a, **k: called.__setitem__("evolution", True) or 0,
        )
        code = review.approve(client, "q-1", as_json=True)
        assert code == 0
        assert called == {"consolidation": True, "evolution": False}

    def test_reject_routes_evolve_to_evolution_path(self, monkeypatch):
        row = self._pending_row("EVOLVE")
        client = self._client_returning(row)
        called = {"c": False, "e": False}
        monkeypatch.setattr(
            review,
            "_reject_consolidation_row",
            lambda *a, **k: called.__setitem__("c", True) or 0,
        )
        monkeypatch.setattr(
            review,
            "_reject_evolution_row",
            lambda *a, **k: called.__setitem__("e", True) or 0,
        )
        code = review.reject(client, "q-1", reason="x", as_json=True)
        assert code == 0
        assert called == {"c": False, "e": True}

    def test_reject_routes_supersede_to_consolidation_path(self, monkeypatch):
        row = self._pending_row("SUPERSEDE_CONSOLIDATION")
        client = self._client_returning(row)
        called = {"c": False, "e": False}
        monkeypatch.setattr(
            review,
            "_reject_consolidation_row",
            lambda *a, **k: called.__setitem__("c", True) or 0,
        )
        monkeypatch.setattr(
            review,
            "_reject_evolution_row",
            lambda *a, **k: called.__setitem__("e", True) or 0,
        )
        code = review.reject(client, "q-1", reason=None, as_json=True)
        assert code == 0
        assert called == {"c": True, "e": False}

    def test_show_diff_routes_by_decision(self, monkeypatch):
        row = self._pending_row(
            "EVOLVE",
            evolution_payload={
                "update_queue_id": "u",
                "candidate_id": "c",
                "target_id": "t",
                "proposals": [],
            },
        )
        client = self._client_returning(row)
        called = {"c": False, "e": False}
        monkeypatch.setattr(
            review,
            "_show_consolidation_diff",
            lambda *a, **k: called.__setitem__("c", True) or 0,
        )
        monkeypatch.setattr(
            review,
            "_show_evolution_diff",
            lambda *a, **k: called.__setitem__("e", True) or 0,
        )
        code = review.show_diff(client, "q-1", as_json=True)
        assert code == 0
        assert called == {"c": False, "e": True}

    def test_approve_unsupported_decision_returns_error(self, capsys):
        row = self._pending_row("ROGUE_DECISION")
        client = self._client_returning(row)
        code = review.approve(client, "q-1", as_json=True)
        assert code == 1
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["status"] == "unsupported_decision"

    def test_approve_not_pending_returns_error(self):
        row = self._pending_row("EVOLVE")
        row["status"] = "approved"
        client = self._client_returning(row)
        code = review.approve(client, "q-1", as_json=False)
        assert code == 1
