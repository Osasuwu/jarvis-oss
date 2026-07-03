"""Handler + resolution + regression guard tests for record_decision.

Covers:
  - TestRecordDecisionInsert — episode row shape and dual-write (#477)
  - TestRecordDecisionResolution — name→UUID resolution through handler
  - test_handler_defined_before_main_entry — import ordering invariant
  - test_decision_made_in_schema_check_constraint — schema enum sync
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server import _handle_record_decision

from test_record_decision_helpers import UID_A, UID_B, UID_C, make_client


class TestRecordDecisionInsert:
    @pytest.mark.asyncio
    async def test_inserts_decision_made_episode(self, monkeypatch):
        client = make_client("ep-42")
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "implement #252 directly",
                "rationale": "additive change, no breaking schema modifications",
                "memories_used": [UID_A, UID_B],
                "outcomes_referenced": ["out-1"],
                "confidence": 0.85,
                "alternatives_considered": ["delegate to agent"],
                "reversibility": "reversible",
                "actor": "skill:delegate",
                "project": "jarvis",
            }
        )

        # Returned message contains episode id
        assert "ep-42" in result[0].text

        # Both legacy episodes and canonical substrate get a write
        # post-#477 (dual-write during cutover wave).
        client.table.assert_any_call("episodes")
        client.table.assert_any_call("events_canonical")
        # Find the episodes-shaped insert (has 'kind', no 'trace_id').
        all_inserts = [
            c.args[0]
            for c in client.table.return_value.insert.call_args_list
            if c.args
        ]
        episode_inserts = [
            p for p in all_inserts if "kind" in p and "trace_id" not in p
        ]
        assert len(episode_inserts) == 1, (
            "expected exactly one episodes insert, got "
            f"{len(episode_inserts)}: {all_inserts!r}"
        )
        insert_arg = episode_inserts[0]
        assert insert_arg["actor"] == "skill:delegate"
        assert insert_arg["kind"] == "decision_made"

        payload = insert_arg["payload"]
        assert payload["decision"] == "implement #252 directly"
        assert payload["rationale"].startswith("additive change")
        # UUIDs pass through, canonicalized (lower-case, hyphenated).
        assert payload["memories_used"] == [UID_A, UID_B]
        assert "memories_used_unresolved" not in payload
        assert payload["outcomes_referenced"] == ["out-1"]
        assert payload["confidence"] == 0.85
        assert payload["alternatives_considered"] == ["delegate to agent"]
        assert payload["reversibility"] == "reversible"
        assert payload["project"] == "jarvis"

    @pytest.mark.asyncio
    async def test_defaults_actor_when_omitted(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "hard",
            }
        )
        insert_arg = client.table.return_value.insert.call_args.args[0]
        assert insert_arg["actor"] == "skill:unknown"

    @pytest.mark.asyncio
    async def test_optional_fields_default_to_empty(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
            }
        )
        payload = client.table.return_value.insert.call_args.args[0]["payload"]
        assert payload["memories_used"] == []
        assert payload["outcomes_referenced"] == []
        assert payload["alternatives_considered"] == []
        # Confidence is omitted when not supplied — don't fabricate a value.
        assert "confidence" not in payload

    @pytest.mark.asyncio
    async def test_intentionally_empty_true_emits_into_payload(self, monkeypatch):
        """#524 — flag is preserved on the episode payload for /learn rate tracking."""
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "no recall data available",
                "reversibility": "reversible",
                "memories_used": [],
                "intentionally_empty": True,
            }
        )

        all_inserts = [
            c.args[0]
            for c in client.table.return_value.insert.call_args_list
            if c.args
        ]
        episode_inserts = [
            p for p in all_inserts if "kind" in p and "trace_id" not in p
        ]
        payload = episode_inserts[0]["payload"]
        assert payload.get("intentionally_empty") is True

    @pytest.mark.asyncio
    async def test_intentionally_empty_omitted_when_false(self, monkeypatch):
        """Default path — flag absent from payload (keeps episodes lean)."""
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": [UID_A],
            }
        )

        all_inserts = [
            c.args[0]
            for c in client.table.return_value.insert.call_args_list
            if c.args
        ]
        episode_inserts = [
            p for p in all_inserts if "kind" in p and "trace_id" not in p
        ]
        payload = episode_inserts[0]["payload"]
        assert "intentionally_empty" not in payload

    @pytest.mark.asyncio
    async def test_db_failure_returns_error_text(self, monkeypatch):
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = RuntimeError(
            "boom: secret-bearing context"
        )
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
            }
        )
        # Privacy: the error surfaces the exception *type* only, never str(exc).
        # A DB-layer error str() can echo the failed row (which carries the
        # caller's free text), so the leaky `{exc}` was replaced by the type.
        assert "RuntimeError" in result[0].text
        assert "boom" not in result[0].text

    @pytest.mark.asyncio
    async def test_secret_in_project_field_blocks_write(self, monkeypatch):
        """#555 round-10 MINOR-2: the decision gate scans ``project`` (it
        persists to episodes.payload.project), so a secret there is rejected —
        no episode insert — matching the store gate's field coverage."""
        client = make_client("ep-blocked")
        monkeypatch.setattr("server._get_client", lambda: client)

        fake_key = "sk-ant-" + "api03-" + "0123456789abcdefghijABCDEFG"
        result = await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "project": fake_key,
            }
        )
        text = result[0].text
        assert "secret_pattern_detected" in text
        assert "api_key_anthropic" in text
        # Privacy: the offending value never appears in the rejection.
        assert fake_key not in text
        # Rejected before the episode write: no insert carries a decision-shaped
        # payload (the only other insert that may fire is the events block-log).
        insert_payloads = [
            c.args[0]
            for c in client.table.return_value.insert.call_args_list
            if c.args
        ]
        assert not any("decision" in p for p in insert_payloads), (
            f"episode was written despite a blocked project field: {insert_payloads!r}"
        )

    @pytest.mark.asyncio
    async def test_secret_in_memories_used_blocks_write(self, monkeypatch):
        """#555 round-10 M1: an unresolved entry in ``memories_used`` is
        preserved verbatim in ``payload.memories_used_unresolved`` and echoed in
        the response, so a secret there bypasses the gate unless scanned. The
        gate must scan ``memories_used`` BEFORE resolution and reject."""
        client = make_client("ep-blocked")
        monkeypatch.setattr("server._get_client", lambda: client)

        fake_key = "sk-ant-" + "api03-" + "0123456789abcdefghijABCDEFG"
        result = await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": [fake_key],
            }
        )
        text = result[0].text
        assert "secret_pattern_detected" in text
        assert "api_key_anthropic" in text
        assert fake_key not in text
        insert_payloads = [
            c.args[0]
            for c in client.table.return_value.insert.call_args_list
            if c.args
        ]
        assert not any("decision" in p for p in insert_payloads), (
            f"episode written despite a blocked memories_used entry: {insert_payloads!r}"
        )

    # ---- End-to-end: memories_used resolution ----

    @pytest.mark.asyncio
    async def test_name_resolves_to_canonical_uuid_in_payload(self, monkeypatch):
        client = make_client("ep-99", name_to_id={"mem-a": UID_A})
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": ["mem-a"],
                "project": "jarvis",
            }
        )
        payload = client.table.return_value.insert.call_args.args[0]["payload"]
        assert payload["memories_used"] == [UID_A]
        assert "memories_used_unresolved" not in payload

    @pytest.mark.asyncio
    async def test_unresolved_names_surface_in_response_and_payload(self, monkeypatch):
        client = make_client("ep-99", name_to_id={})
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": ["ghost-a", "ghost-b"],
            }
        )
        text = result[0].text
        assert "ep-99" in text
        # Warning text must name the unresolved refs so the owner can fix
        # spelling or re-run with a UUID.
        assert "ghost-a" in text and "ghost-b" in text
        payload = client.table.return_value.insert.call_args.args[0]["payload"]
        assert payload["memories_used"] == []
        assert payload["memories_used_unresolved"] == ["ghost-a", "ghost-b"]

    @pytest.mark.asyncio
    async def test_mix_of_uuid_and_name_resolves_both(self, monkeypatch):
        client = make_client("ep-99", name_to_id={"mem-b": UID_B})
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": [UID_A, "mem-b", UID_C],
            }
        )
        payload = client.table.return_value.insert.call_args.args[0]["payload"]
        assert payload["memories_used"] == [UID_A, UID_B, UID_C]
        assert "memories_used_unresolved" not in payload

def test_handler_defined_before_main_entry():
    """Regression guard: `_handle_record_decision` must be bound to the
    server module's namespace BEFORE `if __name__ == "__main__"` triggers.

    When the module runs as main, Python enters ``asyncio.run(main())`` and
    blocks — any def or import after that point never gets bound.  Tests
    don't catch this (they import server as a module, so __main__ never
    fires), but the dispatcher at runtime hits a NameError.

    Pre-#360: the def itself lived in server.py and the assertion was on
    its line ordering.  Post-#360: the def lives in ``handlers/decision.py``
    and is brought into server's namespace via ``from handlers.decision
    import _handle_record_decision``.  The invariant — that binding happens
    before the main guard — is still meaningful, just shifted to the
    import line.
    """
    repo_root = Path(__file__).resolve().parents[1]
    server_path = repo_root / "mcp-memory" / "server.py"
    decision_path = repo_root / "mcp-memory" / "handlers" / "decision.py"
    server_src = server_path.read_text(encoding="utf-8").splitlines()
    decision_src = decision_path.read_text(encoding="utf-8")

    # The def must exist somewhere — handlers/decision.py is the post-#360 home.
    assert "async def _handle_record_decision" in decision_src, (
        "_handle_record_decision def not found in handlers/decision.py"
    )

    # In server.py, the import binding ``_handle_record_decision`` must come
    # before ``if __name__ == "__main__"`` — same regression class as pre-#360,
    # just measured at the binding site (import) rather than the def site.
    binding_line = next(
        (
            i
            for i, line in enumerate(server_src, start=1)
            if "_handle_record_decision" in line
        ),
        None,
    )
    main_guard_line = next(
        (
            i
            for i, line in enumerate(server_src, start=1)
            if line.startswith('if __name__ == "__main__"')
        ),
        None,
    )
    assert binding_line is not None, (
        "no ``from ... import _handle_record_decision`` line found in server.py — "
        "the dispatcher will hit NameError at runtime"
    )
    assert main_guard_line is not None, (
        '``if __name__ == "__main__"`` not found in server.py'
    )
    assert binding_line < main_guard_line, (
        f"_handle_record_decision bound at line {binding_line} is AFTER "
        f'``if __name__ == "__main__"`` at line {main_guard_line} — the binding '
        "will never run when the module starts as __main__."
    )


def test_decision_made_in_schema_check_constraint():
    """Regression guard: schema.sql must include 'decision_made' in episodes.kind CHECK.

    This asserts against the actual schema artifact rather than a Python
    list, so a schema rename or removal would fail the test.
    """
    schema = (
        Path(__file__).resolve().parents[1] / "mcp-memory" / "schema.sql"
    ).read_text()
    lines = [line for line in schema.splitlines() if "check (kind in" in line]
    assert lines, "No 'check (kind in ...)' clause found in schema.sql"
    assert any("'decision_made'" in line for line in lines), (
        "episodes.kind CHECK constraint does not include 'decision_made'"
    )
