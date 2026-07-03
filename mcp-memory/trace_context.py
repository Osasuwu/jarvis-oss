"""Trace propagation primitives for the C17 events substrate (#477).

Implements OTel-style trace_id + parent_event_id propagation via
``contextvars.ContextVar`` so writers can pick up the current trace
without explicit threading through every call site.

Contract (locked in ``docs/design/c17-events-substrate.md`` §3):

1. Owner message → fresh trace_id (skill / hook entry sets it).
2. Scheduled task fire → fresh trace_id.
3. Subagent spawn → inherits parent's trace_id; ``parent_event_id``
   = the spawning event's id.
4. Hook fire → inherits trace_id of the session/task it fires in.

Caller-omitted context: writers MUST NOT crash. ``current_trace`` returns
``(None, None)`` and the writer synthesizes a fresh trace_id with no
parent_event_id. The orphaned-trace pattern (``parent_event_id IS NULL``
on a non-root actor) is itself a queryable signal that propagation is
missing upstream.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

# Internal — readers go through ``current_trace()``; writers go through
# ``with_trace()`` so we control the API surface.
_trace_id_var: ContextVar[Optional[str]] = ContextVar(
    "events_canonical_trace_id", default=None
)
_parent_event_id_var: ContextVar[Optional[str]] = ContextVar(
    "events_canonical_parent_event_id", default=None
)


def new_trace() -> str:
    """Generate a fresh trace_id (canonical UUID hex)."""
    return uuid.uuid4().hex


def current_trace() -> tuple[Optional[str], Optional[str]]:
    """Return ``(trace_id, parent_event_id)`` from the current context.

    Either or both may be ``None`` when no caller has set them — that's
    valid; the writer synthesizes per the design 1-pager §3.
    """
    return _trace_id_var.get(), _parent_event_id_var.get()


@contextmanager
def with_trace(
    trace_id: str, parent_event_id: Optional[str] = None
) -> Iterator[None]:
    """Run a block with a specific trace_id + optional parent_event_id.

    ContextVars are async-safe (per-task) and reset on exit, so nested
    use within asyncio.gather, subagent dispatch, etc. preserves the
    expected scope.

    Example::

        with with_trace(new_trace()):
            await some_writer(...)
    """
    if not _looks_like_uuid_or_hex(trace_id):
        raise ValueError(
            f"trace_id must be a UUID hex string (got {trace_id!r})"
        )
    if parent_event_id is not None and not _looks_like_uuid_or_hex(
        parent_event_id
    ):
        raise ValueError(
            f"parent_event_id must be a UUID hex string (got {parent_event_id!r})"
        )
    trace_token = _trace_id_var.set(trace_id)
    parent_token = _parent_event_id_var.set(parent_event_id)
    try:
        yield
    finally:
        _parent_event_id_var.reset(parent_token)
        _trace_id_var.reset(trace_token)


def _looks_like_uuid_or_hex(s: object) -> bool:
    """Accept canonical UUIDs (8-4-4-4-12) and bare hex (32 chars)."""
    if not isinstance(s, str):
        return False
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False
