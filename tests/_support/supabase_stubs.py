"""Shared test utilities for mcp-memory tests.

Contains reusable stub classes that replace Supabase client patterns
across multiple test modules (test_memory_recall_hook.py,
test_memory_server.py, etc.).
"""

from __future__ import annotations

import types


class StubClient:
    """Minimal supabase-client stand-in for expand_links-style tests.

    Records RPC call params and supports both success and exception paths.
    """

    def __init__(self, *, data=None, raise_exc=None):
        self._data = data or []
        self._raise = raise_exc
        self.rpc_calls: list[tuple[str, dict]] = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return self

    def execute(self):
        if self._raise:
            raise self._raise
        return types.SimpleNamespace(data=self._data)


class TableStub:
    """Supabase table/select-chain stand-in for table-query tests.

    Supports the ``.table().select().eq().not_.is_().limit().execute()``
    chain used by gates and CRUD-style helpers.  ``data`` is the rows
    returned; ``raise_exc`` bubbles through any method to exercise the
    fail-soft path.
    """

    def __init__(self, *, data=None, raise_exc=None):
        self._data = data or []
        self._raise = raise_exc
        self.calls: list[tuple[str, tuple]] = []
        # ``.not_`` is an accessor on the query builder, not a method, so
        # expose it as an attribute that chains back to self.
        self.not_ = self

    def table(self, name):
        self.calls.append(("table", (name,)))
        return self

    def select(self, *cols):
        self.calls.append(("select", cols))
        return self

    def eq(self, col, val):
        self.calls.append(("eq", (col, val)))
        return self

    def is_(self, col, val):
        self.calls.append(("is_", (col, val)))
        return self

    def limit(self, n):
        self.calls.append(("limit", (n,)))
        return self

    def execute(self):
        if self._raise:
            raise self._raise
        return types.SimpleNamespace(data=self._data)


class FakeResp:
    """Minimal response wrapper storing .data for fake client handlers."""

    def __init__(self, data):
        self.data = data


class FakeTableQuery:
    """Chainable fake supporting select/eq/in_/filter/order/limit/is_/update/delete/insert.

    Used by FakeClient to simulate the supabase-py query builder chain.
    Each ``execute()`` call records operation details on the parent and
    delegates to the parent's ``table_handlers`` when configured.
    """

    def __init__(self, parent, table: str):
        self.parent = parent
        self.table_name = table
        self._select = None
        self._filters: list[tuple] = []
        self._order: tuple | None = None
        self._limit: int | None = None
        self._op = "select"
        self._row: dict | list | None = None

    def select(self, columns: str):
        self._op = "select"
        self._select = columns
        return self

    def insert(self, row):
        self._op = "insert"
        self._row = row
        return self

    def update(self, row):
        self._op = "update"
        self._row = row
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def filter(self, col, op, val):
        self._filters.append(("filter", col, op, val))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def order(self, col, *, desc: bool = False):
        self._order = (col, desc)
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def execute(self):
        call = {
            "table": self.table_name,
            "op": self._op,
            "filters": self._filters,
            "select": self._select,
            "order": self._order,
            "limit": self._limit,
            "row": self._row,
        }
        self.parent.table_calls.append(call)
        handler = self.parent.table_handlers.get(self.table_name)
        if handler is not None:
            return FakeResp(handler(call))
        return FakeResp([])


class FakeRPC:
    """Stand-in for supabase-py RPC builder; delegates to parent handlers."""

    def __init__(self, parent, name, params):
        self.parent = parent
        self.name = name
        self.params = params

    def execute(self):
        self.parent.rpc_calls.append({"name": self.name, "params": self.params})
        handler = self.parent.rpc_handlers.get(self.name)
        if handler is not None:
            return FakeResp(handler(self.params))
        return FakeResp(None)


class FakeClient:
    """Stand-in for the supabase-py client with handler-based routing.

    ``rpc_handlers`` / ``table_handlers`` are dicts keyed by name; each
    handler receives the call dict (for tables) or params (for RPCs) and
    returns the ``.data`` payload. Every RPC and table call is recorded
    for test assertions.
    """

    def __init__(self):
        self.rpc_calls: list[dict] = []
        self.table_calls: list[dict] = []
        self.rpc_handlers: dict = {}
        self.table_handlers: dict = {}

    def rpc(self, name, params):
        return FakeRPC(self, name, params)

    def table(self, name):
        return FakeTableQuery(self, name)


def filter_val(call: dict, op: str, col: str):
    """Return the filter value for the first matching (op, col) pair."""
    for f in call["filters"]:
        if f[0] == op and f[1] == col:
            return f[2]
    return None
