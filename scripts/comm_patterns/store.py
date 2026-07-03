"""Supabase store — watermark read/write + comm_patterns insert.

Two tables (per #580 / ADR 0004):
  * ``comm_patterns`` — one row per detected pattern instance.
  * ``comm_patterns_watermark`` — per-(device, session_id) row holding
    ``last_message_idx``. Bumped after each extractor pass to make
    re-runs idempotent (the unique index on
    ``(device, session_id, message_idx)`` is the second line of defence).

The store layer is dependency-thin so unit tests can exercise the
extractor without a live Supabase. The extractor accepts a ``store``
object with three methods (``get_watermark``, ``set_watermark``,
``insert_row``) — the in-memory test double in
``tests/test_comm_patterns_extractor.py`` implements the same interface.
"""

from __future__ import annotations

import os
from typing import Any, Protocol


class Store(Protocol):  # pragma: no cover — typing protocol
    def get_watermark(self, device: str, session_id: str) -> int: ...

    def set_watermark(self, device: str, session_id: str, last_message_idx: int) -> None: ...

    def insert_row(self, row: dict[str, Any]) -> None: ...


def _get_supabase_client():  # pragma: no cover — covered by smoke
    """Lazy import so unit tests don't pay supabase startup cost."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set")
    from supabase import create_client

    return create_client(url, key)


class SupabaseStore:
    """Live Supabase backend. Lazy client init."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):  # pragma: no cover — covered by smoke
        if self._client is None:
            self._client = _get_supabase_client()
        return self._client

    def get_watermark(self, device: str, session_id: str) -> int:  # pragma: no cover
        resp = (
            self.client.table("comm_patterns_watermark")
            .select("last_message_idx")
            .eq("device", device)
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return -1
        return int(rows[0].get("last_message_idx", -1))

    def set_watermark(self, device: str, session_id: str, last_message_idx: int) -> None:  # pragma: no cover
        # Composite-key upsert — schema PRIMARY KEY(device, session_id).
        self.client.table("comm_patterns_watermark").upsert(
            {
                "device": device,
                "session_id": session_id,
                "last_message_idx": last_message_idx,
            },
            on_conflict="device,session_id",
        ).execute()

    def insert_row(self, row: dict[str, Any]) -> None:  # pragma: no cover
        # ON CONFLICT DO NOTHING via the composite unique index — re-runs
        # produce zero duplicate rows even if the watermark was lost.
        self.client.table("comm_patterns").upsert(
            row,
            on_conflict="device,session_id,message_idx",
            ignore_duplicates=True,
        ).execute()


class InMemoryStore:
    """Test double — same Protocol, no network.

    Holds rows + watermarks in dicts. Useful in tests *and* in the
    backfill --dry-run mode.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.watermarks: dict[tuple[str, str], int] = {}
        # Mirror the live unique-index for O(1) dedup checks; the linear
        # scan was fine per session but quadratic on backfill --dry-run.
        self._row_keys: set[tuple[str, str, int]] = set()

    def get_watermark(self, device: str, session_id: str) -> int:
        return self.watermarks.get((device, session_id), -1)

    def set_watermark(self, device: str, session_id: str, last_message_idx: int) -> None:
        self.watermarks[(device, session_id)] = last_message_idx

    def insert_row(self, row: dict[str, Any]) -> None:
        key = (row["device"], row["session_id"], row["message_idx"])
        if key in self._row_keys:
            return
        self._row_keys.add(key)
        self.rows.append(dict(row))
