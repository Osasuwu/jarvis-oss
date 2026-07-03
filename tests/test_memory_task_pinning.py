"""Fire-and-forget task pinning in mcp-memory/handlers/memory.py.

CPython holds only a *weak* reference to the task returned by
``asyncio.create_task``; without an external strong ref the GC can collect a
detached task mid-flight, silently dropping its work. memory.py routes every
fire-and-forget task (recall touch/backfill/recall-event, store-path
auto-link/known-unknown resolution) through ``_pin_task`` so the task is held
in ``_PENDING_TASKS`` until it completes.

This mirrors write_scrubber._PENDING_BLOCK_LOGS and decision._PENDING_TASKS.
Pre-existing tech debt surfaced during #555 review (sibling-grep).

conftest.py stubs the MCP SDK + Supabase before ``handlers.memory`` imports.
"""

from __future__ import annotations

import asyncio
import gc
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Import ``server`` first so the server <-> handlers.memory import chain is
# fully initialised before we grab the handler module directly (importing
# handlers.memory first triggers a partially-initialised circular import —
# the same reason sibling tests import ``from server import ...``).
import server  # noqa: F401
import handlers.memory as mem
from recall import RecallHit


# ---------------------------------------------------------------------------
# _pin_task contract: pinned task survives GC and is discarded on completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_task_holds_until_completion_then_discards():
    gate = asyncio.Event()
    completed = asyncio.Event()

    async def _gated():
        await gate.wait()
        completed.set()

    mem._PENDING_TASKS.clear()
    # Pin the task, then drop the only local strong ref and force a GC pass.
    # Without pinning, the task would be collectable here; with it, the
    # module-level set keeps it alive.
    mem._pin_task(asyncio.create_task(_gated()))
    del _gated
    gc.collect()

    assert len(mem._PENDING_TASKS) == 1, "task must be strong-reffed while in flight"

    # Release the gate and let the task run to completion.
    gate.set()
    await asyncio.wait_for(completed.wait(), timeout=1.0)
    # The done-callback runs on the loop after the task finishes; yield to it.
    await asyncio.sleep(0)

    assert mem._PENDING_TASKS == set(), "completed task must be unpinned by the done-callback"


# ---------------------------------------------------------------------------
# Handler-level: _hybrid_recall's recall-event emit (the legacy fire-and-forget
# anti-pattern, memory.py:354) is now pinned rather than bare-detached.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_recall_pins_emit_recall_event(monkeypatch):
    gate = asyncio.Event()

    async def _gated_emit(_client, _payload):
        await gate.wait()

    async def _stub_recall(*_args, **_kwargs):
        return [
            RecallHit(
                memory={
                    "id": "mem-1",
                    "name": "m",
                    "type": "project",
                    "description": "d",
                    "content": "c",
                    "updated_at": "2026-06-19T00:00:00+00:00",
                    "similarity": 0.9,
                },
                semantic_score=0.9,
                keyword_score=0.0,
                rrf_score=0.5,
                temporal_score=0.5,
                final_score=0.5,
                source="semantic",
            )
        ]

    monkeypatch.setattr(mem, "recall", _stub_recall)
    monkeypatch.setattr(mem, "_emit_recall_event", _gated_emit)

    mem._PENDING_TASKS.clear()
    await mem._hybrid_recall(
        MagicMock(), query_text="anything", project="jarvis", mem_type=None, limit=5
    )

    # The emit task is still gated (pending) — it must be pinned, not GC-droppable.
    gc.collect()
    assert len(mem._PENDING_TASKS) == 1

    gate.set()
    # Drain the pinned task so it doesn't leak into other tests.
    await asyncio.gather(*list(mem._PENDING_TASKS))
    await asyncio.sleep(0)
    assert mem._PENDING_TASKS == set()


# ---------------------------------------------------------------------------
# Source guard: no bare asyncio.create_task survives in memory.py. Every call
# must be wrapped by _pin_task so the GC-drop regression can't silently return
# (sibling-grep discipline — see CLAUDE.md "No silent caps").
# ---------------------------------------------------------------------------


def test_every_create_task_is_pinned():
    src = (Path(mem.__file__)).read_text(encoding="utf-8")

    total = len(re.findall(r"asyncio\.create_task\(", src))
    pinned = len(re.findall(r"_pin_task\(\s*asyncio\.create_task\(", src))

    assert total >= 5, f"expected >=5 detached tasks in memory.py, found {total}"
    assert pinned == total, (
        f"{total - pinned} bare asyncio.create_task call(s) in memory.py are not "
        "wrapped by _pin_task — they can be GC-collected mid-flight. Wrap them."
    )
