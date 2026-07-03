"""Unit tests for memory_calibration_summary handler (#251).

Exercises the rendered markdown output and RPC failure path. The SQL
view + RPC logic itself is verified against the schema artifact.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server import _handle_memory_calibration_summary


def _mock_client_returning(rpc_data):
    client = MagicMock()
    client.rpc.return_value.execute.return_value = MagicMock(data=rpc_data)
    return client


class TestMemoryCalibrationSummary:
    @pytest.mark.asyncio
    async def test_renders_overall_and_by_type(self, monkeypatch):
        rpc_data = [{
            "overall_brier": 0.12,
            "total_memories": 17,
            "by_type": [
                {
                    "type": "decision",
                    "n": 10,
                    "brier": 0.30,
                    "avg_predicted": 0.85,
                    "avg_actual": 0.40,
                    "over_confident": True,
                    "under_confident": False,
                },
                {
                    "type": "feedback",
                    "n": 7,
                    "brier": 0.05,
                    "avg_predicted": 0.70,
                    "avg_actual": 0.75,
                    "over_confident": False,
                    "under_confident": False,
                },
            ],
            "warnings": ["decision: brier=0.300 (overconfident)"],
        }]
        client = _mock_client_returning(rpc_data)
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_memory_calibration_summary({"project": "jarvis"})
        text = result[0].text

        # RPC called with correct parameter shape
        client.rpc.assert_called_with(
            "memory_calibration_summary", {"p_project": "jarvis"}
        )

        # Overall + counts surfaced
        assert "Overall Brier" in text
        assert "0.120" in text
        assert "17" in text

        # Per-type rows present
        assert "decision" in text
        assert "feedback" in text

        # Over-confidence flag surfaces
        assert "overconfident" in text.lower()

        # Warning section rendered
        assert "Warnings" in text
        assert "decision: brier=0.300" in text

    @pytest.mark.asyncio
    async def test_global_scope_maps_project_to_null(self, monkeypatch):
        client = _mock_client_returning([{
            "overall_brier": 0.0,
            "total_memories": 0,
            "by_type": [],
            "warnings": [],
        }])
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_memory_calibration_summary({"project": "global"})
        # 'global' is convention for "no project filter" — RPC should get None
        client.rpc.assert_called_with(
            "memory_calibration_summary", {"p_project": None}
        )

    @pytest.mark.asyncio
    async def test_empty_data_returns_friendly_message(self, monkeypatch):
        client = _mock_client_returning([{
            "overall_brier": 0.0,
            "total_memories": 0,
            "by_type": [],
            "warnings": [],
        }])
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_memory_calibration_summary({})
        assert "No calibration data yet" in result[0].text

    @pytest.mark.asyncio
    async def test_rpc_failure_surfaces_error(self, monkeypatch):
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError("rpc blew up")
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_memory_calibration_summary({})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].type == "text"
        assert "Error calling memory_calibration_summary" in result[0].text
        assert "rpc blew up" in result[0].text

    @pytest.mark.asyncio
    async def test_handles_dict_shape_rpc_response(self, monkeypatch):
        """Some Supabase clients return a single dict; handler must cope."""
        client = _mock_client_returning({
            "overall_brier": 0.2,
            "total_memories": 3,
            "by_type": [],
            "warnings": [],
        })
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_memory_calibration_summary({})
        assert "Overall Brier" in result[0].text


def test_schema_has_calibration_view_and_rpc():
    """Regression guard: schema.sql must define the view + RPC the handler calls."""
    schema = (Path(__file__).resolve().parents[1] / "mcp-memory" / "schema.sql").read_text()
    # View
    assert "create or replace view memory_calibration" in schema, \
        "memory_calibration view missing from schema.sql"
    # RPC
    assert "create or replace function memory_calibration_summary" in schema, \
        "memory_calibration_summary RPC missing from schema.sql"
    # FK column on task_outcomes — required for outcomes to link to memories
    assert "memory_id uuid references memories(id)" in schema, \
        "task_outcomes.memory_id FK missing — calibration view would always be empty"
