"""Validation tests for record_decision (#252, #325).

Exercises the real handler in mcp-memory/server.py with a mock Supabase
client — asserts that missing / malformed parameters are rejected before
any table write.
"""

from __future__ import annotations

import pytest

from server import _handle_record_decision

from test_record_decision_helpers import make_client


class TestRecordDecisionValidation:
    @pytest.mark.asyncio
    async def test_missing_decision_errors(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)
        result = await _handle_record_decision(
            {
                "rationale": "because reasons",
                "reversibility": "reversible",
            }
        )
        assert "decision is required" in result[0].text.lower()
        assert not client.table.called

    @pytest.mark.asyncio
    async def test_missing_rationale_errors(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)
        result = await _handle_record_decision(
            {
                "decision": "pick X",
                "reversibility": "reversible",
            }
        )
        assert "rationale is required" in result[0].text.lower()
        assert not client.table.called

    @pytest.mark.asyncio
    async def test_invalid_reversibility_errors(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)
        result = await _handle_record_decision(
            {
                "decision": "pick X",
                "rationale": "because",
                "reversibility": "permanent",  # not in enum
            }
        )
        assert "reversibility" in result[0].text.lower()
        assert not client.table.called

    @pytest.mark.asyncio
    async def test_confidence_out_of_range_errors(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)
        for bad in (-0.1, 1.1, 2.0):
            result = await _handle_record_decision(
                {
                    "decision": "pick X",
                    "rationale": "because",
                    "reversibility": "reversible",
                    "confidence": bad,
                }
            )
            assert "confidence" in result[0].text.lower()
        assert not client.table.called
