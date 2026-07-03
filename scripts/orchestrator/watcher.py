"""Orchestrator watcher daemon — poll events, gate on quota, dispatch /rework.

Tick interface (the deep module's only contract)::

    tick(now, events_client, locks_client, spawner) -> TickResult

Per-poll (every 30-60s):
1. Poll the events table for ``event_type ∈ {review_negative}`` AND
   ``processed=false``.
2. Gate on quota cache. If ``weekly_pct >= 80``, skip dispatch entirely and
   do NOT mark events processed (next poll re-evaluates).
3. Group by PR number. Per-PR lock check via ``outcome_records``
   (pattern_tags includes ``pr-<N>`` + ``rework`` + ``in_flight``, TTL 2h).
   If lock exists, mark events processed (subsumed) without dispatch.
4. Otherwise acquire lock and spawn ``claude -p "/rework <N>"``.
   On spawn success: mark events processed. On spawn failure: leave events
   unprocessed (next poll retries).

Daemon usage::

    python scripts/orchestrator/watcher.py [--poll-interval 45]

Environment:
    SUPABASE_URL  — Supabase project URL
    SUPABASE_KEY  — Supabase anon key

Realizes decision ``e6441d77-457b-451a-ac48-06830c1d9a8a`` (Q3 — orchestrator
architecture, AFK PR-rework loop).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_POLL_INTERVAL_SECONDS = 45
LOCK_TTL_MINUTES = 120
QUOTA_WEEKLY_THRESHOLD_PCT = 80
EVENT_TYPES: list[str] = ["review_negative"]

_LOG_DIR = Path.home() / ".jarvis" / "orchestrator"
QUOTA_CACHE_DEFAULT = _LOG_DIR / "usage.json"
LOG_PATH_DEFAULT = _LOG_DIR / "watcher.log"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EventRow:
    """A single row from the events table."""

    event_id: str
    event_type: str
    payload: dict
    repo: str


@dataclass
class TickResult:
    """Summary of one tick cycle."""

    events_processed: int = 0
    events_skipped_quota: int = 0
    events_skipped_lock: int = 0
    spawns_attempted: int = 0
    spawns_succeeded: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract interfaces (injected dependencies — swap for tests)
# ---------------------------------------------------------------------------


class EventsClient:
    """Interface for reading / writing the events table.

    Production: :class:`SupabaseEventsClient`.
    Tests: provide a fake that records method calls.
    """

    def fetch_unprocessed(self, event_types: list[str]) -> list[EventRow]:
        """Return all unprocessed rows whose event_type is in *event_types*."""
        raise NotImplementedError

    def mark_processed(self, event_id: str) -> None:
        """Mark a single event as processed."""
        raise NotImplementedError

    def mark_many_processed(self, event_ids: list[str]) -> None:
        """Mark multiple events as processed (batch)."""
        raise NotImplementedError


class LocksClient:
    """Interface for per-PR rework locks via ``outcome_records``.

    A "lock" is a ``task_outcomes`` row with::

        pattern_tags @> ARRAY['pr-<N>', 'rework', 'in_flight']

    with ``created_at`` within the TTL window (default 2h).

    Production: :class:`SupabaseLocksClient`.
    Tests: provide a fake that records method calls.
    """

    def exists(self, pr_number: int) -> bool:
        """Return True if an active in_flight lock exists for this PR."""
        raise NotImplementedError

    def acquire(self, pr_number: int) -> bool:
        """Try to acquire a lock. Returns False if already held or insert failed."""
        raise NotImplementedError


Spawner = Callable[[int], None]
"""Callable that takes a PR number and spawns ``claude -p "/rework <N>"``.

Must raise on failure — the tick function catches the exception and leaves
the event row unprocessed so the next poll retries.
"""


# ---------------------------------------------------------------------------
# Quota check (pure function)
# ---------------------------------------------------------------------------


def check_quota(cache_path: Path = QUOTA_CACHE_DEFAULT) -> bool:
    """Return True if dispatch is allowed (weekly usage < 80%).

    Returns False (fail-closed) when the cache file:
    - does not exist
    - contains malformed JSON
    - is missing the ``weekly_pct`` key
    - has ``weekly_pct >= 80``
    """
    if not cache_path.exists():
        return False
    try:
        data = json.loads(cache_path.read_text())
        weekly_pct = data.get("weekly_pct")
        if weekly_pct is None:
            return False
        return weekly_pct < QUOTA_WEEKLY_THRESHOLD_PCT
    except (json.JSONDecodeError, OSError):
        return False


# ---------------------------------------------------------------------------
# Tick implementation
# ---------------------------------------------------------------------------


def _extract_pr_number(payload: dict) -> Optional[int]:
    """Extract a PR number from an event payload.

    Checks ``pr_number`` then ``pull_request_number`` (legacy keys).
    Returns None when neither key is present.
    """
    pr = payload.get("pr_number") or payload.get("pull_request_number")
    if pr is not None:
        return int(pr)
    return None


def tick(
    now: datetime,  # noqa: ARG001 — reserved for future time-window checks
    events_client: EventsClient,
    locks_client: LocksClient,
    spawner: Spawner,
    quota_cache_path: Path = QUOTA_CACHE_DEFAULT,
) -> TickResult:
    """One poll-gate-lock-dispatch cycle.

    Steps:
    1. Fetch unprocessed events of configured types from the events table.
    2. Gate on quota — if weekly usage >= 80%, skip entirely (no marks).
    3. Group rows by PR number (deduplicates within a single tick).
    4. For each PR with a lock active → mark rows processed (subsumed).
    5. For each PR without a lock → acquire lock, spawn ``/rework``.
       - Spawn succeeds → mark rows processed.
       - Spawn raises → leave rows unprocessed (next tick retries).
    """
    result = TickResult()

    # 1. Fetch
    rows = events_client.fetch_unprocessed(EVENT_TYPES)
    if not rows:
        return result

    # 2. Quota gate
    if not check_quota(quota_cache_path):
        result.events_skipped_quota = len(rows)
        return result

    # 3. Group by PR number
    by_pr: dict[int, list[EventRow]] = {}
    for row in rows:
        pr = _extract_pr_number(row.payload)
        if pr is None:
            # Can't determine PR — mark processed to avoid re-polling garbage
            events_client.mark_processed(row.event_id)
            result.events_processed += 1
            continue
        by_pr.setdefault(pr, []).append(row)

    # 4. Dispatch per PR
    for pr_number, pr_rows in by_pr.items():
        event_ids = [r.event_id for r in pr_rows]

        # 4a. Check existing lock
        if locks_client.exists(pr_number):
            events_client.mark_many_processed(event_ids)
            result.events_skipped_lock += len(event_ids)
            result.events_processed += len(event_ids)
            continue

        # 4b. Try to acquire lock
        if not locks_client.acquire(pr_number):
            # Another process got there first
            events_client.mark_many_processed(event_ids)
            result.events_skipped_lock += len(event_ids)
            result.events_processed += len(event_ids)
            continue

        # 4c. Dispatch
        result.spawns_attempted += 1
        try:
            spawner(pr_number)
            events_client.mark_many_processed(event_ids)
            result.events_processed += len(event_ids)
            result.spawns_succeeded += 1
        except Exception as exc:
            # Spawn failed — do NOT mark processed; next poll retries
            result.errors.append(f"PR #{pr_number}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Production implementations (Supabase)
# ---------------------------------------------------------------------------


def _supabase_client():
    """Lazy-import and return a Supabase client.

    The ``supabase`` package is heavy and only needed by the production
    implementations — tests provide their own fakes.
    """
    from supabase import create_client

    return create_client


class SupabaseEventsClient(EventsClient):
    """EventsClient backed by the legacy ``events`` table.

    The ``events`` table has ``event_type``, ``processed``, and ``payload``
    columns needed by the watcher. When ``events_canonical`` gains these
    columns (#739), a new implementation can be swapped in without changing
    the tick function.
    """

    def __init__(self, url: str, key: str):
        self._table = _supabase_client()(url, key).table("events")

    def fetch_unprocessed(self, event_types: list[str]) -> list[EventRow]:
        resp = (
            self._table.select("id, event_type, payload, repo")
            .in_("event_type", event_types)
            .eq("processed", False)
            .execute()
        )
        rows: list[EventRow] = []
        for r in getattr(resp, "data", []) or []:
            rows.append(
                EventRow(
                    event_id=r["id"],
                    event_type=r.get("event_type", ""),
                    payload=r.get("payload", {}),
                    repo=r.get("repo", ""),
                )
            )
        return rows

    def mark_processed(self, event_id: str) -> None:
        self._table.update({"processed": True}).eq("id", event_id).execute()

    def mark_many_processed(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        self._table.update({"processed": True}).in_("id", event_ids).execute()


class SupabaseLocksClient(LocksClient):
    """LocksClient backed by ``task_outcomes`` table (pattern_tags convention).

    Lock = a ``task_outcomes`` row with::

        pattern_tags @> ARRAY['pr-<N>', 'rework', 'in_flight']
        AND created_at > now() - TTL

    The lock is both a mutual-exclusion primitive and an audit trail — it
    persists across container restarts, so even if the watcher crashes, the
    lock survives to prevent double-dispatch from a new watcher instance.
    """

    def __init__(
        self,
        url: str,
        key: str,
        ttl_minutes: int = LOCK_TTL_MINUTES,
    ):
        self._table = _supabase_client()(url, key).table("task_outcomes")
        self._ttl = timedelta(minutes=ttl_minutes)

    def _lock_tags(self, pr_number: int) -> list[str]:
        return [f"pr-{pr_number}", "rework", "in_flight"]

    def exists(self, pr_number: int) -> bool:
        cutoff = (datetime.now(timezone.utc) - self._ttl).isoformat()
        resp = (
            self._table.select("id")
            .contains("pattern_tags", self._lock_tags(pr_number))
            .gte("created_at", cutoff)
            .limit(1)
            .execute()
        )
        data = getattr(resp, "data", []) or []
        return len(data) > 0

    def acquire(self, pr_number: int) -> bool:
        if self.exists(pr_number):
            return False
        try:
            self._table.insert(
                {
                    "task_type": "autonomous",
                    "task_description": f"rework lock for PR #{pr_number}",
                    "outcome_status": "pending",
                    "project": "jarvis",
                    "pattern_tags": self._lock_tags(pr_number),
                    "source_provenance": f"sandcastle:watcher:lock:{pr_number}",
                }
            ).execute()
            return True
        except Exception:  # noqa: BLE001
            return False


def process_spawner(pr_number: int) -> None:
    """Spawn a Claude Code session to run ``/rework <N>``.

    The spawned process is intentionally *not* waited on for status beyond
    exit code — all judgement happens inside the on-demand session.
    """
    cmd = ["claude", "-p", f"/rework {pr_number}"]
    result = subprocess.run(  # noqa: S603 — claude is a first-party binary
        cmd,
        capture_output=False,
        timeout=3600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p '/rework {pr_number}' exited {result.returncode}"
        )


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------


def _setup_logging(log_path: Path = LOG_PATH_DEFAULT) -> logging.Logger:
    """Configure rotating file logger + stderr console handler."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("watcher")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers on re-entry (testing)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def run_daemon(
    poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    quota_cache_path: Path = QUOTA_CACHE_DEFAULT,
    log_path: Path = LOG_PATH_DEFAULT,
) -> None:
    """Enter the watcher's main poll loop.

    The daemon never exits on a transient error — each tick failure is logged
    and the loop continues. This is intentional: the daemon should only be
    stopped by Task Scheduler / OS signals, never by an event-processing crash.
    """
    logger = _setup_logging(log_path)
    logger.info("Watcher daemon starting (poll_interval=%ds)", poll_interval)

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL and SUPABASE_KEY must be set")
        sys.exit(1)

    events_client: EventsClient = SupabaseEventsClient(supabase_url, supabase_key)
    locks_client: LocksClient = SupabaseLocksClient(supabase_url, supabase_key)

    logger.info("Watcher initialized, entering poll loop")

    while True:
        tick_start = time.monotonic()
        try:
            result = tick(
                now=datetime.now(timezone.utc),
                events_client=events_client,
                locks_client=locks_client,
                spawner=process_spawner,
                quota_cache_path=quota_cache_path,
            )
            if result.spawns_succeeded > 0 or result.errors:
                logger.info(
                    "Tick: %d spawned, %d processed, "
                    "%d quota-skipped, %d lock-skipped, %d errors",
                    result.spawns_succeeded,
                    result.events_processed,
                    result.events_skipped_quota,
                    result.events_skipped_lock,
                    len(result.errors),
                )
                for err in result.errors:
                    logger.error("Tick error: %s", err)
        except Exception:  # noqa: BLE001
            logger.exception("Tick crashed (daemon continues)")

        elapsed = time.monotonic() - tick_start
        sleep_for = max(1, poll_interval - int(elapsed))
        time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Orchestrator watcher daemon — poll, gate, lock, dispatch"
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Seconds between ticks (default: %d)" % DEFAULT_POLL_INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--quota-cache",
        type=str,
        default=str(QUOTA_CACHE_DEFAULT),
        help="Path to quota cache JSON file",
    )
    parser.add_argument(
        "--log-path",
        type=str,
        default=str(LOG_PATH_DEFAULT),
        help="Path to watcher log file",
    )
    args = parser.parse_args()

    run_daemon(
        poll_interval=args.poll_interval,
        quota_cache_path=Path(args.quota_cache),
        log_path=Path(args.log_path),
    )


if __name__ == "__main__":
    main()
