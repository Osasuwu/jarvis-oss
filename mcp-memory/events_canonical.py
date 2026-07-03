"""C17 events_canonical writer + transient-failure buffer (#477).

The first writer wired to this substrate is ``record_decision``
(``handlers/decision.py``). Subsequent writers (memory_write, tool_call,
error, etc.) follow in the substrate-consumer wave.

Design (``docs/design/c17-events-substrate.md`` §4 — Write semantics):

Happy path:
    1. Caller invokes :func:`emit_event` with action / actor / payload.
    2. Writer drains any buffered events first (so chronology is best-
       effort preserved), each tagged ``degraded=true``.
    3. Writer inserts the new event row.
    4. After-insert trigger fires ``pg_notify`` on channel
       ``events_canonical``.
    5. Writer returns the inserted row dict.

Degraded path:
    1. INSERT fails (transient pg outage, RLS misconfig, etc.).
    2. Writer logs a warning to stderr (visible in MCP server logs).
    3. Writer pushes the failed payload onto a bounded ring buffer
       (~100 events). Overflow drops the oldest with a ``signal_dropped``
       log line.
    4. Writer returns ``None`` to caller — **the original action does
       not fail.**
    5. On the next successful insert, the buffer drains; replayed rows
       carry ``degraded=true`` so dashboards / cost reconciliation can
       exclude them.

This keeps ``record_decision`` (and future writers) from coupling to
substrate availability — a transient outage does not lose the original
event, nor does it crash the calling skill / hook.
"""

from __future__ import annotations

import collections
import logging
import sys
from typing import Any, Optional

from trace_context import current_trace, new_trace

_BUFFER_MAX = 100
_buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=_BUFFER_MAX)

logger = logging.getLogger(__name__)
# Default to stderr so MCP server logs surface the warnings — the host
# may layer additional handlers, those compose on top.
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[events_canonical] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.WARNING)


def emit_event(
    client: Any,
    *,
    actor: str,
    action: str,
    payload: Optional[dict[str, Any]] = None,
    outcome: Optional[str] = None,
    cost_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    parent_event_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Insert an event into ``events_canonical`` (with degraded fallback).

    ``trace_id`` defaults to the current ContextVar value, or a fresh
    synthesized id when nothing is set. ``parent_event_id`` likewise
    falls back to the ContextVar — caller-passed values win.

    Returns the inserted row (dict) on success, ``None`` on failure
    (event was buffered for retry on next call). The caller MUST treat
    a ``None`` return as "do not fail downstream" — this contract is
    why ``record_decision`` continues even when substrate is degraded.
    """
    ctx_trace, ctx_parent = current_trace()
    final_trace = trace_id or ctx_trace or new_trace()
    final_parent = parent_event_id or ctx_parent

    row = {
        "trace_id": final_trace,
        "parent_event_id": final_parent,
        "actor": actor,
        "action": action,
        "payload": payload or {},
    }
    if outcome is not None:
        row["outcome"] = outcome
    if cost_tokens is not None:
        row["cost_tokens"] = cost_tokens
    if cost_usd is not None:
        row["cost_usd"] = cost_usd

    # Always try to drain buffered events FIRST so they land before the
    # current one (best-effort chronology). Buffered rows carry their
    # original ``ts`` if the DB supports it; we rely on the DEFAULT now()
    # for un-stamped rows, which means buffered rows get a "drain time"
    # ts — acceptable for Sprint 35 substrate POC, refine in 1.x.
    _drain_buffer(client)

    try:
        result = client.table("events_canonical").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — substrate must not propagate
        logger.warning(
            "INSERT failed (action=%s actor=%s): %s — buffering for retry",
            action,
            actor,
            exc,
        )
        _enqueue(row)
        return None

    data = getattr(result, "data", None)
    if not isinstance(data, list) or not data:
        # Insert call returned without a row — defensive: also buffer.
        logger.warning(
            "INSERT returned empty data (action=%s actor=%s) — buffering", action, actor
        )
        _enqueue(row)
        return None

    return data[0]


def _enqueue(row: dict[str, Any]) -> None:
    """Push a row onto the buffer, logging when overflow drops oldest."""
    if len(_buffer) == _buffer.maxlen:
        dropped = _buffer[0]
        logger.warning(
            "buffer full (max=%d) — dropping oldest action=%s actor=%s",
            _BUFFER_MAX,
            dropped.get("action"),
            dropped.get("actor"),
        )
    _buffer.append(row)


def _drain_buffer(client: Any) -> int:
    """Try to insert every buffered row with ``degraded=true``.

    Returns the number drained. On per-row failure, the row stays in
    the buffer for a future drain attempt — there is no "give up after
    N tries" because the caller already returned success and the buffer
    is the only place this event still exists.
    """
    if not _buffer:
        return 0

    drained = 0
    # Snapshot length so we don't loop on rows re-enqueued by failure.
    pending = len(_buffer)
    for _ in range(pending):
        row = _buffer.popleft()
        replay = {**row, "degraded": True}
        try:
            client.table("events_canonical").insert(replay).execute()
            drained += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "drain failed (action=%s actor=%s): %s — re-buffering",
                replay.get("action"),
                replay.get("actor"),
                exc,
            )
            # Push back to the right side so order with newer events
            # is preserved. Note: maxlen behavior may drop us if buffer
            # is now full of newer entries; that's correct (degraded
                # signal already lost).
            _buffer.append(row)
            return drained
    return drained


# Test-only helpers — exposed for unit tests, NOT part of the runtime
# contract. Names start with the visible-private convention so any
# accidental import from production code triggers review attention.


def _buffer_len_for_test() -> int:
    return len(_buffer)


def _buffer_clear_for_test() -> None:
    _buffer.clear()
