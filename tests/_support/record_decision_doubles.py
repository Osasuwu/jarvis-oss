"""Shared test doubles for record_decision tests.

Replaces deep MagicMock chains with contract-style doubles that enforce
real response shapes — used by test_record_decision_validation.py,
test_record_decision_handler.py, and test_record_decision_lib.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# Stable UUIDs — chosen so uuid.UUID() produces a consistent canonical form.
UID_A = "11111111-1111-1111-1111-111111111111"
UID_B = "22222222-2222-2222-2222-222222222222"
UID_C = "33333333-3333-3333-3333-333333333333"


def make_client(inserted_id: str = "ep-1",
                name_to_id: dict[str, str] | None = None
                ) -> MagicMock:
    """Build a Supabase client test double with insert + name-lookup paths.

    Shortcut for handler tests that exercise the full record_decision flow:
      - ``table("episodes").insert(...).execute()`` → ``{"data": [{...}]}``
      - ``table("memories").select(...).eq(name=X)...`` → resolved ID or empty
    """
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": inserted_id}]
    )

    lookup = dict(name_to_id or {})

    def _select_side_effect(*_args, **_kwargs):
        chain = MagicMock()

        def _eq_name(column, value):
            hit = lookup.get(value) if column == "name" else None
            leaf = MagicMock()
            leaf.data = [{"id": hit}] if hit else []
            tail = MagicMock()
            tail.is_.return_value.order.return_value.limit.return_value.execute.return_value = leaf
            tail.is_.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = leaf
            tail.eq.return_value.is_.return_value.order.return_value.limit.return_value.execute.return_value = leaf
            return tail

        chain.eq.side_effect = _eq_name
        return chain

    client.table.return_value.select.side_effect = _select_side_effect
    return client


def resolver_client(name_to_id: dict[str, str] | None = None,
                    project_capture: list | None = None
                    ) -> MagicMock:
    """Build a client double for direct ``_resolve_memory_refs`` calls.

    ``project_capture``: if provided, every ``.eq("project", v)`` appends
    ``v`` to the list — lets tests assert project scoping reached the query.
    """
    name_to_id = dict(name_to_id or {})
    client = MagicMock()

    def _select_side_effect(*_args, **_kwargs):
        chain = MagicMock()

        def _eq_name(column, value):
            assert column == "name", "_resolve_memory_refs must key on 'name'"
            hit = name_to_id.get(value)
            leaf = MagicMock()
            leaf.data = [{"id": hit}] if hit else []

            head = MagicMock()
            head.is_.return_value.order.return_value.limit.return_value.execute.return_value = leaf

            def _project_eq(col, val):
                if col == "project" and project_capture is not None:
                    project_capture.append(val)
                scoped = MagicMock()
                scoped.order.return_value.limit.return_value.execute.return_value = leaf
                return scoped

            head.is_.return_value.eq.side_effect = _project_eq
            return head

        chain.eq.side_effect = _eq_name
        return chain

    client.table.return_value.select.side_effect = _select_side_effect
    return client
