"""Unit tests for orchestrator watcher tick function (#639).

All tests use fakes for EventsClient, LocksClient, and Spawner — no live
Supabase or Claude session is required.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest

from orchestrator.watcher import (
    EventRow,
    EventsClient,
    LocksClient,
    TickResult,
    check_quota,
    tick,
)


# ============================================================================
# Fakes
# ============================================================================


@dataclass
class FakeEventsClient(EventsClient):
    """In-memory events store. Records method calls for assertion."""

    rows: list[EventRow] = field(default_factory=list)
    processed: set[str] = field(default_factory=set)
    mark_calls: list[str] = field(default_factory=list)

    def fetch_unprocessed(self, event_types: list[str]) -> list[EventRow]:
        return [
            r
            for r in self.rows
            if r.event_type in event_types and r.event_id not in self.processed
        ]

    def mark_processed(self, event_id: str) -> None:
        self.mark_calls.append(event_id)
        self.processed.add(event_id)

    def mark_many_processed(self, event_ids: list[str]) -> None:
        self.mark_calls.extend(event_ids)
        self.processed.update(event_ids)


@dataclass
class FakeLocksClient(LocksClient):
    """In-memory lock store. Records method calls for assertion."""

    locks: set[int] = field(default_factory=set)
    exists_calls: list[int] = field(default_factory=list)
    acquire_calls: list[int] = field(default_factory=list)
    _acquire_result: bool | None = None  # None = use default logic

    def exists(self, pr_number: int) -> bool:
        self.exists_calls.append(pr_number)
        return pr_number in self.locks

    def acquire(self, pr_number: int) -> bool:
        self.acquire_calls.append(pr_number)
        if self._acquire_result is not None:
            return self._acquire_result
        if pr_number in self.locks:
            return False
        self.locks.add(pr_number)
        return True


@dataclass
class FakeSpawner:
    """Records spawn calls. Can be configured to raise."""

    calls: list[int] = field(default_factory=list)
    raise_on: int | None = None  # PR number that should cause a raise

    def __call__(self, pr_number: int) -> None:
        self.calls.append(pr_number)
        if self.raise_on is not None and pr_number == self.raise_on:
            raise RuntimeError(f"spawn failed for PR #{pr_number}")


# ============================================================================
# Helpers
# ============================================================================


def make_row(
    event_id: str,
    event_type: str = "review_negative",
    pr_number: int = 42,
    repo: str = "Osasuwu/jarvis",
) -> EventRow:
    return EventRow(
        event_id=event_id,
        event_type=event_type,
        payload={"pr_number": pr_number},
        repo=repo,
    )


def now() -> datetime:
    return datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def quota_file(tmp_path: Path) -> Path:
    """Create a temporary quota cache file with 50% usage (under threshold)."""
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"weekly_pct": 50}))
    return path


# ============================================================================
# AC: check_quota (pure function)
# ============================================================================


class TestCheckQuota:
    def test_under_threshold_allows(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"weekly_pct": 50}))
        assert check_quota(path) is True

    def test_at_threshold_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"weekly_pct": 80}))
        assert check_quota(path) is False

    def test_over_threshold_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"weekly_pct": 85}))
        assert check_quota(path) is False

    def test_missing_file_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        assert check_quota(path) is False

    def test_malformed_json_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text("not json")
        assert check_quota(path) is False

    def test_missing_weekly_pct_key_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"other": "data"}))
        assert check_quota(path) is False


# ============================================================================
# AC: Tick — empty queue
# ============================================================================


class TestTickEmptyQueue:
    """Empty queue → no spawn, no marks."""

    def test_no_events(self, quota_file: Path) -> None:
        events = FakeEventsClient()  # no rows
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.events_processed == 0
        assert result.spawns_attempted == 0
        assert result.spawns_succeeded == 0
        assert result.errors == []
        assert spawner.calls == []

    def test_wrong_event_type_filtered(self, quota_file: Path) -> None:
        events = FakeEventsClient(rows=[make_row("e1", event_type="ci_failure")])
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.events_processed == 0
        assert spawner.calls == []


# ============================================================================
# AC: Tick — one review_negative row, quota OK, no lock
# ============================================================================


class TestTickNormalDispatch:
    """One review_negative row, quota OK, no lock → exactly one spawner call with
    correct args; row marked processed."""

    def test_dispatch_and_mark(self, quota_file: Path) -> None:
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_attempted == 1
        assert result.spawns_succeeded == 1
        assert result.events_processed == 1
        assert spawner.calls == [42]
        assert "e1" in events.processed


# ============================================================================
# AC: Tick — quota blocks dispatch
# ============================================================================


class TestTickQuotaGate:
    """Quota cache shows 85% → no spawner call, row NOT marked."""

    def test_quota_blocks(self, tmp_path: Path) -> None:
        quota = tmp_path / "usage.json"
        quota.write_text(json.dumps({"weekly_pct": 85}))
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota)

        assert result.events_skipped_quota == 1
        assert result.spawns_attempted == 0
        assert spawner.calls == []
        assert "e1" not in events.processed

    def test_quota_missing_file_fails_closed(self, tmp_path: Path) -> None:
        quota = tmp_path / "nonexistent.json"
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota)

        assert result.events_skipped_quota == 1
        assert result.spawns_attempted == 0
        assert spawner.calls == []

    def test_quota_ok_then_below_threshold_dispatches(self, quota_file: Path) -> None:
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_succeeded == 1
        assert spawner.calls == [42]


# ============================================================================
# AC: Tick — in-flight lock subsumes
# ============================================================================


class TestTickLockSubsumes:
    """In-flight lock present for PR N → row marked processed, no spawner call."""

    def test_lock_prevents_dispatch(self, quota_file: Path) -> None:
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient(locks={42})  # lock already held
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.events_skipped_lock == 1
        assert result.events_processed == 1
        assert result.spawns_attempted == 0
        assert spawner.calls == []
        assert "e1" in events.processed


# ============================================================================
# AC: Tick — spawner raises → row stays unprocessed
# ============================================================================


class TestTickSpawnFailure:
    """Spawner raises → row stays processed=false, no lock written."""

    def test_spawn_error_leaves_row(self, quota_file: Path) -> None:
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient()
        spawner = FakeSpawner(raise_on=42)

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_attempted == 1
        assert result.spawns_succeeded == 0
        assert result.events_processed == 0
        assert len(result.errors) == 1
        assert "42" in result.errors[0]
        assert spawner.calls == [42]
        assert "e1" not in events.processed


# ============================================================================
# AC: Tick — dedup within tick
# ============================================================================


class TestTickDedup:
    """Two rows for the same PR in one tick → exactly one spawner call,
    both rows marked processed."""

    def test_same_pr_dedup(self, quota_file: Path) -> None:
        events = FakeEventsClient(
            rows=[
                make_row("e1", pr_number=42),
                make_row("e2", pr_number=42),
            ]
        )
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_attempted == 1
        assert result.spawns_succeeded == 1
        assert result.events_processed == 2
        assert spawner.calls == [42]
        assert "e1" in events.processed
        assert "e2" in events.processed

    def test_different_prs_separate_spawns(self, quota_file: Path) -> None:
        events = FakeEventsClient(
            rows=[
                make_row("e1", pr_number=42),
                make_row("e2", pr_number=100),
            ]
        )
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_attempted == 2
        assert result.spawns_succeeded == 2
        assert result.events_processed == 2
        assert spawner.calls == [42, 100]

    def test_same_pr_different_events_one_lock_subsumes_all(
        self, quota_file: Path
    ) -> None:
        events = FakeEventsClient(
            rows=[
                make_row("e1", pr_number=42),
                make_row("e2", pr_number=42),
            ]
        )
        locks = FakeLocksClient(locks={42})
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_attempted == 0
        assert result.events_processed == 2
        assert result.events_skipped_lock == 2
        assert spawner.calls == []


# ============================================================================
# AC: Tick — event without PR number
# ============================================================================


class TestTickNoPrNumber:
    """Events without a PR number are marked processed without dispatch."""

    def test_missing_pr_number(self, quota_file: Path) -> None:
        events = FakeEventsClient(
            rows=[
                EventRow(
                    event_id="e1",
                    event_type="review_negative",
                    payload={},  # no pr_number
                    repo="Osasuwu/jarvis",
                )
            ]
        )
        locks = FakeLocksClient()
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.events_processed == 1
        assert result.spawns_attempted == 0
        assert spawner.calls == []
        assert "e1" in events.processed


# ============================================================================
# AC: Tick — lock acquire race
# ============================================================================


class TestTickLockRace:
    """Lock acquire returns False (race with another process)."""

    def test_acquire_race(self, quota_file: Path) -> None:
        events = FakeEventsClient(rows=[make_row("e1", pr_number=42)])
        locks = FakeLocksClient()
        locks._acquire_result = False  # race: another process got lock
        spawner = FakeSpawner()

        result = tick(now(), events, locks, spawner, quota_cache_path=quota_file)

        assert result.spawns_attempted == 0
        assert result.events_processed == 1
        assert result.events_skipped_lock == 1
        assert spawner.calls == []
        assert "e1" in events.processed
