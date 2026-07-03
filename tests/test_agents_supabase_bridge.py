"""Unit tests for ``agents.supabase_client`` with a mocked Supabase client.

The smoke suite only covers imports/surface/credential failure. These tests
pin the semantics the bridge promises to mirror from ``mcp-memory/server.py``:

1. Reads never surface soft-deleted memories (``deleted_at IS NOT NULL``).
2. Project-scoped reads include global (NULL-project) memories via ``or_()``.
3. ``list_goals`` orders by priority then deadline (NULLs last).
4. Writes that affect no rows raise loudly — no silent no-ops
   (``store_event``, ``mark_event_processed``).
5. ``update_goal_progress`` parses JSON-string progress and retries on
   optimistic-concurrency conflicts before surrendering.
6. ``audit`` is best-effort — backend failures must never propagate.

The supabase-py builder returns ``self`` from every chain method until
``execute()``. ``_FakeQuery`` mirrors that contract and records every call,
so tests can assert on both the chained filters and the final payload.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("supabase")


class _FakeResult:
    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self.data = data if data is not None else []


class _FakeQuery:
    """Chainable stand-in for a supabase-py query builder."""

    def __init__(
        self,
        calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]],
        result: _FakeResult | Exception,
    ) -> None:
        self._calls = calls
        self._result = result

    def execute(self) -> _FakeResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    def __getattr__(self, name: str):
        def chain(*args: Any, **kwargs: Any) -> _FakeQuery:
            self._calls.append((name, args, kwargs))
            return self

        return chain


class _FakeClient:
    """Queueable stand-in for a supabase-py client.

    Pre-load per-table results with ``preset``; each ``table(name)`` call
    pops the next result. Recorded call lists are available under
    ``chains[name]`` (list of per-query chains, in call order).
    """

    def __init__(self) -> None:
        self._presets: dict[str, list[_FakeResult | Exception]] = {}
        self.chains: dict[str, list[list[tuple[str, tuple[Any, ...], dict[str, Any]]]]] = {}

    def preset(self, table: str, *results: _FakeResult | Exception) -> None:
        self._presets.setdefault(table, []).extend(results)

    def table(self, name: str) -> _FakeQuery:
        chain: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.chains.setdefault(name, []).append(chain)
        pending = self._presets.setdefault(name, [])
        result = pending.pop(0) if pending else _FakeResult([])
        return _FakeQuery(chain, result)


def _names(chain: list[tuple[str, tuple[Any, ...], dict[str, Any]]]) -> list[str]:
    return [c[0] for c in chain]


def _find(
    chain: list[tuple[str, tuple[Any, ...], dict[str, Any]]], name: str
) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    for call in chain:
        if call[0] == name:
            return call
    raise AssertionError(f"method {name!r} was not called; chain={chain!r}")


# -- list_memories ---------------------------------------------------------


def test_list_memories_excludes_soft_deleted() -> None:
    """Soft-deleted rows must never leak to agents — MCP parity."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("memories", _FakeResult(data=[{"id": 1}]))

    rows = supabase_client.list_memories(client=cli)

    assert rows == [{"id": 1}]
    is_call = _find(cli.chains["memories"][0], "is_")
    assert is_call[1] == ("deleted_at", "null"), is_call


def test_list_memories_project_filter_includes_global() -> None:
    """`project=X` must OR-in global (NULL-project) memories, not strict-eq."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("memories", _FakeResult(data=[]))

    supabase_client.list_memories(project="jarvis", client=cli)

    chain = cli.chains["memories"][0]
    or_call = _find(chain, "or_")
    (query_string,) = or_call[1]
    assert "project.eq.jarvis" in query_string, query_string
    assert "project.is.null" in query_string, query_string
    # Must NOT also apply a strict .eq("project", ...) — that would exclude globals.
    offenders = [c for c in chain if c[0] == "eq" and c[1][:1] == ("project",)]
    assert not offenders, offenders


def test_list_memories_without_project_skips_project_filter() -> None:
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("memories", _FakeResult(data=[]))

    supabase_client.list_memories(client=cli)

    chain = cli.chains["memories"][0]
    assert "or_" not in _names(chain)
    assert not any(c[0] == "eq" and c[1][:1] == ("project",) for c in chain)


def test_list_memories_type_filter_uses_strict_eq() -> None:
    """Type filter is strict (no global fallback) — matches MCP."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("memories", _FakeResult(data=[]))

    supabase_client.list_memories(type="feedback", client=cli)

    chain = cli.chains["memories"][0]
    type_eq = [c for c in chain if c[0] == "eq" and c[1][:1] == ("type",)]
    assert type_eq and type_eq[0][1] == ("type", "feedback"), type_eq


# -- list_goals ------------------------------------------------------------


def test_list_goals_orders_priority_then_deadline_nullslast() -> None:
    """Ordering must mirror ``_handle_goal_list`` in mcp-memory/server.py."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("goals", _FakeResult(data=[]))

    supabase_client.list_goals(client=cli)

    chain = cli.chains["goals"][0]
    orders = [c for c in chain if c[0] == "order"]
    assert len(orders) == 2, orders
    assert orders[0][1] == ("priority",), orders[0]
    assert orders[1][1] == ("deadline",), orders[1]
    # NULL deadlines must sort last: open-ended goals fall behind dated ones.
    assert orders[1][2].get("nullsfirst") is False, orders[1]


# -- store_event -----------------------------------------------------------


def test_store_event_raises_when_supabase_returns_no_row() -> None:
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("events", _FakeResult(data=[]))

    with pytest.raises(RuntimeError, match="no row after inserting"):
        supabase_client.store_event(event_type="github.Foo", repo="o/r", title="t", client=cli)


def test_store_event_inserts_full_payload() -> None:
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("events", _FakeResult(data=[{"id": "new"}]))

    row = supabase_client.store_event(
        event_type="github.IssuesEvent",
        repo="o/r",
        title="t",
        severity="medium",
        payload={"foo": 1},
        source="langgraph-monitor",
        client=cli,
    )

    assert row == {"id": "new"}
    insert_call = _find(cli.chains["events"][0], "insert")
    (payload,) = insert_call[1]
    assert payload["event_type"] == "github.IssuesEvent"
    assert payload["severity"] == "medium"
    assert payload["source"] == "langgraph-monitor"
    assert payload["payload"] == {"foo": 1}
    # No dedup_key ⇒ plain insert path; upsert must NOT be used.
    assert "upsert" not in _names(cli.chains["events"][0])


def test_store_event_with_dedup_key_upserts_on_conflict() -> None:
    """A dedup_key MUST drive an ``upsert(on_conflict="dedup_key")`` (CRITICAL #1011 r2).

    The round-1 code used a plain ``insert`` and leaned on a swallowed 409 to
    absorb re-observed terminal events. That swallowing also masked genuine
    insert errors. An explicit upsert makes a true duplicate succeed cleanly
    (idempotent re-observation) while real failures still propagate.
    """
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("events", _FakeResult(data=[{"id": "dedup-row"}]))

    row = supabase_client.store_event(
        event_type="task_done",
        repo="o/r",
        title="t",
        dedup_key="task_done:abc123:a2",
        client=cli,
    )

    assert row == {"id": "dedup-row"}
    chain = cli.chains["events"][0]
    # upsert, not insert, and the conflict target is the dedup_key column.
    assert "insert" not in _names(chain)
    upsert_call = _find(chain, "upsert")
    (payload,) = upsert_call[1]
    assert payload["dedup_key"] == "task_done:abc123:a2"
    assert upsert_call[2].get("on_conflict") == "dedup_key"


def test_store_event_with_dedup_key_raises_when_no_row() -> None:
    """Upsert that returns no row still fails loud — no silent dedup no-op."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("events", _FakeResult(data=[]))

    with pytest.raises(RuntimeError, match="no row"):
        supabase_client.store_event(
            event_type="task_done",
            repo="o/r",
            title="t",
            dedup_key="task_done:abc123:a2",
            client=cli,
        )


# -- mark_event_processed --------------------------------------------------


def test_mark_event_processed_raises_when_no_row_matched() -> None:
    """Silent no-op would mask a stale id or wrong-environment lookup."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("events", _FakeResult(data=[]))

    with pytest.raises(RuntimeError, match="Event not found or not updated"):
        supabase_client.mark_event_processed("does-not-exist", processed_by="test", client=cli)


def test_mark_event_processed_filters_by_id_and_succeeds() -> None:
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("events", _FakeResult(data=[{"id": "abc"}]))

    supabase_client.mark_event_processed("abc", processed_by="test", client=cli)

    eq_call = _find(cli.chains["events"][0], "eq")
    assert eq_call[1] == ("id", "abc")


# -- update_goal_progress --------------------------------------------------


def test_update_goal_progress_parses_json_string_progress() -> None:
    """Progress returned as a JSON string must be parsed, not reset to []."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset(
        "goals",
        _FakeResult(
            data=[
                {
                    "progress": '[{"item": "existing"}]',
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            ]
        ),
        _FakeResult(data=[{"slug": "g"}]),
    )

    supabase_client.update_goal_progress("g", {"item": "new"}, client=cli)

    # chains["goals"][0] is the SELECT; [1] is the UPDATE we want to inspect.
    update_call = _find(cli.chains["goals"][1], "update")
    (payload,) = update_call[1]
    assert payload["progress"] == [{"item": "existing"}, {"item": "new"}]


def test_update_goal_progress_retries_on_optimistic_conflict() -> None:
    """On no-row-match, re-read and retry with the fresh updated_at."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset(
        "goals",
        _FakeResult(data=[{"progress": [], "updated_at": "t1"}]),  # read 1
        _FakeResult(data=[]),  # update 1 lost
        _FakeResult(data=[{"progress": [], "updated_at": "t2"}]),  # read 2
        _FakeResult(data=[{"slug": "g"}]),  # update 2 won
    )

    supabase_client.update_goal_progress("g", {"new": True}, client=cli)

    chains = cli.chains["goals"]
    assert len(chains) == 4, [_names(c) for c in chains]

    # Each update's .eq("updated_at", …) predicate must match the last read.
    first_update_pred = [c for c in chains[1] if c[0] == "eq" and c[1][:1] == ("updated_at",)]
    second_update_pred = [c for c in chains[3] if c[0] == "eq" and c[1][:1] == ("updated_at",)]
    assert first_update_pred and first_update_pred[0][1] == ("updated_at", "t1")
    assert second_update_pred and second_update_pred[0][1] == ("updated_at", "t2")


def test_update_goal_progress_raises_after_retries_exhausted() -> None:
    from agents import supabase_client

    cli = _FakeClient()
    for i in range(3):
        cli.preset(
            "goals",
            _FakeResult(data=[{"progress": [], "updated_at": f"t-{i}"}]),
            _FakeResult(data=[]),  # update never wins
        )

    with pytest.raises(RuntimeError, match="Concurrent update prevented"):
        supabase_client.update_goal_progress("g", {"x": 1}, client=cli, max_retries=3)


def test_update_goal_progress_raises_when_goal_missing() -> None:
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("goals", _FakeResult(data=[]))

    with pytest.raises(RuntimeError, match="Goal not found"):
        supabase_client.update_goal_progress("nope", {"x": 1}, client=cli)


# -- audit -----------------------------------------------------------------


def test_audit_swallows_backend_errors() -> None:
    """audit() is best-effort; a failing backend must not propagate."""
    from agents import supabase_client

    class _BoomClient:
        def table(self, _name: str) -> None:
            raise RuntimeError("db down")

    # Absence of exception is the assertion.
    supabase_client.audit(
        agent_id="langgraph-monitor",
        tool_name="event_monitor",
        action="poll",
        client=_BoomClient(),  # type: ignore[arg-type]
    )


def test_audit_writes_agent_id_and_defaults() -> None:
    """agent_id is the actor differentiator vs. MCP writes (which leave it NULL)."""
    from agents import supabase_client

    cli = _FakeClient()
    cli.preset("audit_log", _FakeResult(data=[{"id": 1}]))

    supabase_client.audit(
        agent_id="langgraph-monitor",
        tool_name="event_monitor",
        action="poll",
        target="o/r",
        client=cli,
    )

    insert_call = _find(cli.chains["audit_log"][0], "insert")
    (payload,) = insert_call[1]
    assert payload["agent_id"] == "langgraph-monitor"
    assert payload["tool_name"] == "event_monitor"
    assert payload["action"] == "poll"
    assert payload["target"] == "o/r"
    assert payload["outcome"] == "success"  # default
    assert payload["details"] == {}  # default
