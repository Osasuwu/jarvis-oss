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

from record_decision_doubles import UID_A, UID_B, UID_C, make_client


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
        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        assert len(episode_inserts) == 1, (
            f"expected exactly one episodes insert, got {len(episode_inserts)}: {all_inserts!r}"
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

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
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

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        payload = episode_inserts[0]["payload"]
        assert "intentionally_empty" not in payload

    @pytest.mark.asyncio
    async def test_session_id_persisted_into_payload(self, monkeypatch):
        """#1269 — sanitized session_id lands in the episode payload AND
        flows into the events_canonical dual-write (payload is copied)."""
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)

        sid = "fe22ddae-340c-4c5b-b8d7-82a4df8396ee"
        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": [UID_A],
                "session_id": sid,
            }
        )

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        assert episode_inserts[0]["payload"]["session_id"] == sid

        canonical_inserts = [p for p in all_inserts if "trace_id" in p]
        assert canonical_inserts, "dual-write to events_canonical expected"
        assert canonical_inserts[0]["payload"]["session_id"] == sid

    @pytest.mark.asyncio
    async def test_invalid_session_id_omitted_write_succeeds(self, monkeypatch):
        """#1269 — malformed sid never fails the write; it is just dropped."""
        client = make_client("ep-77")
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": [UID_A],
                "session_id": "not a valid sid!",
            }
        )
        assert "ep-77" in result[0].text

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        assert "session_id" not in episode_inserts[0]["payload"]

    @pytest.mark.asyncio
    async def test_absent_session_id_omitted(self, monkeypatch):
        """#1269 — backward compatible: no sid arg → no payload key."""
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

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        assert "session_id" not in episode_inserts[0]["payload"]

    @pytest.mark.asyncio
    async def test_cwd_persisted_into_payload(self, monkeypatch):
        """#1423 — cwd lands in the episode payload AND the events_canonical
        dual-write; it is the recovery-key component alongside project+since,
        replacing session_id in that role."""
        client = make_client()
        monkeypatch.setattr("server._get_client", lambda: client)

        cwd = "/home/user/jarvis/.claude/worktrees/issue-1423-e026a4"
        await _handle_record_decision(
            {
                "decision": "x",
                "rationale": "y",
                "reversibility": "reversible",
                "memories_used": [UID_A],
                "cwd": cwd,
            }
        )

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        assert episode_inserts[0]["payload"]["cwd"] == cwd

        canonical_inserts = [p for p in all_inserts if "trace_id" in p]
        assert canonical_inserts, "dual-write to events_canonical expected"
        assert canonical_inserts[0]["payload"]["cwd"] == cwd

    @pytest.mark.asyncio
    async def test_absent_cwd_omitted(self, monkeypatch):
        """#1423 — backward compatible: no cwd arg → no payload key."""
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

        all_inserts = [c.args[0] for c in client.table.return_value.insert.call_args_list if c.args]
        episode_inserts = [p for p in all_inserts if "kind" in p and "trace_id" not in p]
        assert "cwd" not in episode_inserts[0]["payload"]

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
            c.args[0] for c in client.table.return_value.insert.call_args_list if c.args
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
            c.args[0] for c in client.table.return_value.insert.call_args_list if c.args
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
    repo_root = Path(__file__).resolve().parents[2]
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
        (i for i, line in enumerate(server_src, start=1) if "_handle_record_decision" in line),
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
    assert main_guard_line is not None, '``if __name__ == "__main__"`` not found in server.py'
    assert binding_line < main_guard_line, (
        f"_handle_record_decision bound at line {binding_line} is AFTER "
        f'``if __name__ == "__main__"`` at line {main_guard_line} — the binding '
        "will never run when the module starts as __main__."
    )


"""#1269 — decision_list handler + registration + schema index."""


class _FakeQuery:
    """Chainable query double recording eq/order/limit calls."""

    def __init__(self, rows):
        self._rows = rows
        self.eq_calls: list[tuple[str, object]] = []
        self.order_calls: list = []
        self.limit_value = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eq_calls.append((column, value))
        return self

    def order(self, *args, **kwargs):
        self.order_calls.append((args, kwargs))
        return self

    def limit(self, n):
        self.limit_value = n
        return self

    def execute(self):
        return MagicMock(data=self._rows)


def _make_list_client(rows):
    client = MagicMock()
    query = _FakeQuery(rows)
    client.table.return_value = query
    return client, query


class _FakeFilterableQuery:
    """Chainable query double that actually applies eq/gte/order/limit to a
    fixed row set — needed to assert on real filter *semantics* (AC #1423),
    not just which chain calls were made."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.eq_calls: list[tuple[str, object]] = []
        self.gte_calls: list[tuple[str, object]] = []
        self.order_calls: list = []
        self.limit_value = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eq_calls.append((column, value))
        if column.startswith("payload->>"):
            key = column.split(">>", 1)[1]
            self._rows = [r for r in self._rows if (r.get("payload") or {}).get(key) == value]
        return self

    def gte(self, column, value):
        self.gte_calls.append((column, value))
        if column == "created_at":
            self._rows = [r for r in self._rows if r.get("created_at") >= value]
        return self

    def order(self, column, desc=False, **_kwargs):
        self.order_calls.append((column, desc))
        self._rows = sorted(self._rows, key=lambda r: r.get("created_at"), reverse=desc)
        return self

    def limit(self, n):
        self.limit_value = n
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return MagicMock(data=self._rows)


def _make_filterable_list_client(rows):
    """Client whose .table() returns a FRESH filterable query per call, so
    successive _handle_decision_list invocations in one test don't leak
    filtered state into each other."""
    client = MagicMock()
    queries: list[_FakeFilterableQuery] = []

    def _table(_name):
        q = _FakeFilterableQuery(rows)
        queries.append(q)
        return q

    client.table.side_effect = _table
    return client, queries


_SID = "fe22ddae-340c-4c5b-b8d7-82a4df8396ee"


class TestDecisionList:
    @pytest.mark.asyncio
    async def test_returns_stamped_decisions(self, monkeypatch):
        from server import _handle_decision_list

        rows = [
            {
                "id": "ep-1",
                "created_at": "2026-07-30T10:00:00+00:00",
                "payload": {"decision": "use payload jsonb", "session_id": _SID},
            },
            {
                "id": "ep-2",
                "created_at": "2026-07-30T11:00:00+00:00",
                "payload": {"decision": "expression index", "session_id": _SID},
            },
        ]
        client, query = _make_list_client(rows)
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list({"session_id": _SID})
        text = result[0].text
        assert "ep-1" in text and "ep-2" in text
        assert "use payload jsonb" in text
        client.table.assert_called_once_with("episodes")
        assert ("kind", "decision_made") in query.eq_calls
        assert ("payload->>session_id", _SID) in query.eq_calls

    @pytest.mark.asyncio
    async def test_requires_valid_session_id(self, monkeypatch):
        from server import _handle_decision_list

        client, _ = _make_list_client([])
        monkeypatch.setattr("server._get_client", lambda: client)

        for bad in ({}, {"session_id": "not a sid!"}, {"session_id": None}):
            result = await _handle_decision_list(bad)
            assert "session_id" in result[0].text
            client.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_project_filter_applied(self, monkeypatch):
        from server import _handle_decision_list

        client, query = _make_list_client([])
        monkeypatch.setattr("server._get_client", lambda: client)

        await _handle_decision_list({"session_id": _SID, "project": "jarvis"})
        assert ("payload->>project", "jarvis") in query.eq_calls

    @pytest.mark.asyncio
    async def test_empty_result_says_so(self, monkeypatch):
        from server import _handle_decision_list

        client, _ = _make_list_client([])
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list({"session_id": _SID})
        assert "No decisions" in result[0].text

    @pytest.mark.asyncio
    async def test_project_alone_without_session_id_succeeds(self, monkeypatch):
        """#1423 — session_id is optional; project alone is a valid recovery key."""
        from server import _handle_decision_list

        client, _ = _make_list_client([])
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list({"project": "jarvis"})
        assert "Error" not in result[0].text
        client.table.assert_called_once_with("episodes")

    @pytest.mark.asyncio
    async def test_malformed_session_id_with_project_falls_back_to_project(self, monkeypatch):
        """Malformed session_id is silently dropped (existing sanitize contract,
        not a new error path) — project alone still satisfies the recovery-key
        requirement."""
        from server import _handle_decision_list

        client, query = _make_list_client([])
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list({"session_id": "not a sid!", "project": "jarvis"})
        assert "Error" not in result[0].text
        assert all(col != "payload->>session_id" for col, _ in query.eq_calls)

    @pytest.mark.asyncio
    async def test_neither_session_id_nor_project_errors(self, monkeypatch):
        """#1423 AC — neither session_id nor project ⇒ error, never a full scan."""
        from server import _handle_decision_list

        client, _ = _make_list_client([])
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list({})
        assert "Error" in result[0].text
        client.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_since_window_returns_decisions_across_session_ids(self, monkeypatch):
        """#1423 AC — two different session_ids, same project+cwd, both inside
        the window: a (project, cwd, since) query returns BOTH; a session_id-
        scoped query returns only the one matching."""
        from server import _handle_decision_list

        rows = [
            {
                "id": "ep-aaa",
                "created_at": "2026-08-07T10:00:00+00:00",
                "payload": {
                    "decision": "d1",
                    "session_id": "sid-aaa",
                    "project": "jarvis",
                    "cwd": "/repo",
                },
            },
            {
                "id": "ep-bbb",
                "created_at": "2026-08-07T11:00:00+00:00",
                "payload": {
                    "decision": "d2",
                    "session_id": "sid-bbb",
                    "project": "jarvis",
                    "cwd": "/repo",
                },
            },
        ]
        client, _queries = _make_filterable_list_client(rows)
        monkeypatch.setattr("server._get_client", lambda: client)

        window_result = await _handle_decision_list(
            {"project": "jarvis", "cwd": "/repo", "since": "24h"}
        )
        window_text = window_result[0].text
        assert "ep-aaa" in window_text and "ep-bbb" in window_text

        scoped_result = await _handle_decision_list({"session_id": "sid-aaa", "project": "jarvis"})
        scoped_text = scoped_result[0].text
        assert "ep-aaa" in scoped_text
        assert "ep-bbb" not in scoped_text

    @pytest.mark.asyncio
    async def test_cwd_filter_excludes_other_cwd(self, monkeypatch):
        """#1423 AC — a decision from a different cwd, same project, same
        window, is NOT returned when cwd is passed."""
        from server import _handle_decision_list

        rows = [
            {
                "id": "ep-here",
                "created_at": "2026-08-07T10:00:00+00:00",
                "payload": {"decision": "d1", "project": "jarvis", "cwd": "/repo-a"},
            },
            {
                "id": "ep-elsewhere",
                "created_at": "2026-08-07T11:00:00+00:00",
                "payload": {"decision": "d2", "project": "jarvis", "cwd": "/repo-b"},
            },
        ]
        client, _queries = _make_filterable_list_client(rows)
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list(
            {"project": "jarvis", "cwd": "/repo-a", "since": "2026-08-01T00:00:00+00:00"}
        )
        text = result[0].text
        assert "ep-here" in text
        assert "ep-elsewhere" not in text

    @pytest.mark.asyncio
    async def test_limit_returns_newest_first(self, monkeypatch):
        """#1423 AC — with more decisions than limit, the returned set is the
        NEWEST `limit`, and ordering is newest-first."""
        from server import _handle_decision_list

        rows = [
            {
                "id": "ep-old",
                "created_at": "2026-08-01T00:00:00+00:00",
                "payload": {"decision": "old", "project": "jarvis"},
            },
            {
                "id": "ep-mid",
                "created_at": "2026-08-05T00:00:00+00:00",
                "payload": {"decision": "mid", "project": "jarvis"},
            },
            {
                "id": "ep-new",
                "created_at": "2026-08-07T00:00:00+00:00",
                "payload": {"decision": "new", "project": "jarvis"},
            },
        ]
        client, queries = _make_filterable_list_client(rows)
        monkeypatch.setattr("server._get_client", lambda: client)

        result = await _handle_decision_list({"project": "jarvis", "limit": 2})
        text = result[0].text
        assert "ep-new" in text and "ep-mid" in text
        assert "ep-old" not in text
        assert text.index("ep-new") < text.index("ep-mid")
        assert queries[-1].order_calls == [("created_at", True)]

    def test_tool_registered_in_schema(self):
        import inspect

        from tools_schema import tool_definitions

        src = inspect.getsource(tool_definitions)
        assert "decision_list" in src
        # record_decision schema surfaces the optional session_id param.
        assert "session_id" in src

    def test_server_dispatches_decision_list(self):
        import inspect

        import server as server_module

        src = inspect.getsource(server_module.call_tool)
        assert 'name == "decision_list"' in src


def test_session_id_expression_index_in_schema():
    """#1269 — schema.sql carries the partial expression index used by
    decision_list's payload->>session_id filter."""
    schema = (Path(__file__).resolve().parents[2] / "mcp-memory" / "schema.sql").read_text()
    assert "payload->>'session_id'" in schema, (
        "schema.sql missing the session_id expression index for decision_list"
    )


def test_decision_made_in_schema_check_constraint():
    """Regression guard: schema.sql must include 'decision_made' in episodes.kind CHECK.

    This asserts against the actual schema artifact rather than a Python
    list, so a schema rename or removal would fail the test.
    """
    schema = (Path(__file__).resolve().parents[2] / "mcp-memory" / "schema.sql").read_text()
    lines = [line for line in schema.splitlines() if "check (kind in" in line]
    assert lines, "No 'check (kind in ...)' clause found in schema.sql"
    assert any("'decision_made'" in line for line in lines), (
        "episodes.kind CHECK constraint does not include 'decision_made'"
    )
