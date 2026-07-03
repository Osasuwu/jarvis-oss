"""Unit tests for the C17 trace propagation primitives (#477)."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

# Add mcp-memory to sys.path so flat-module imports work like in production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-memory"))

from trace_context import (  # noqa: E402
    current_trace,
    new_trace,
    with_trace,
)


def test_new_trace_is_canonical_uuid_hex() -> None:
    tid = new_trace()
    # 32 hex chars, parseable by uuid.UUID.
    assert len(tid) == 32
    assert uuid.UUID(tid)


def test_new_trace_returns_unique_ids() -> None:
    seen = {new_trace() for _ in range(100)}
    assert len(seen) == 100


def test_default_context_is_none_pair() -> None:
    """Outside any with_trace block, both vars are None."""
    assert current_trace() == (None, None)


def test_with_trace_sets_and_resets() -> None:
    tid = new_trace()
    pid = new_trace()
    assert current_trace() == (None, None)
    with with_trace(tid, parent_event_id=pid):
        assert current_trace() == (tid, pid)
    assert current_trace() == (None, None)


def test_with_trace_no_parent_event_id() -> None:
    tid = new_trace()
    with with_trace(tid):
        got_trace, got_parent = current_trace()
        assert got_trace == tid
        assert got_parent is None


def test_with_trace_nests() -> None:
    outer = new_trace()
    inner = new_trace()
    with with_trace(outer):
        assert current_trace() == (outer, None)
        with with_trace(inner, parent_event_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
            assert current_trace() == (
                inner,
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            )
        # Exiting inner restores outer (with the original None parent).
        assert current_trace() == (outer, None)


def test_with_trace_rejects_non_uuid_trace_id() -> None:
    with pytest.raises(ValueError):
        with with_trace("not-a-uuid"):
            pass  # pragma: no cover


def test_with_trace_rejects_non_uuid_parent_event_id() -> None:
    with pytest.raises(ValueError):
        with with_trace(new_trace(), parent_event_id="bogus"):
            pass  # pragma: no cover


def test_with_trace_accepts_canonical_uuid_string() -> None:
    """uuid.UUID() canonical form (8-4-4-4-12) also accepted."""
    canonical = str(uuid.uuid4())
    with with_trace(canonical):
        got_trace, _ = current_trace()
        assert got_trace == canonical


@pytest.mark.asyncio
async def test_async_tasks_have_isolated_context() -> None:
    """Per-task ContextVar isolation — concurrent tasks see their own
    trace_id, not each other's."""
    seen: list[tuple[str, str]] = []

    async def worker(label: str, tid: str) -> None:
        with with_trace(tid):
            await asyncio.sleep(0)  # yield to other tasks
            got, _ = current_trace()
            seen.append((label, got))

    a = new_trace()
    b = new_trace()
    await asyncio.gather(worker("A", a), worker("B", b))

    assert sorted(seen) == sorted([("A", a), ("B", b)])
