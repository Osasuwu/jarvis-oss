#!/usr/bin/env python3
"""Advance global task sources — tick due rows into events queue.

Intended to run every 5 min via Task Scheduler (Workshop only).

Idempotent single-transaction advancer:
- SELECT due rows WHERE enabled AND (next_run IS NULL OR next_run <= now()) FOR UPDATE SKIP LOCKED
- INSERT events rows (dedup on sha256('global_task:'||mode||':'||id||':'||EXTRACT(EPOCH FROM next_run)),
  where mode is the lapse namespace 'coalesce' | 'fire' — see compute_dedup_key)
- UPDATE last_run, next_run, enabled
- Handles both coalesce (one event per due row) and fire_per_interval (bounded to 24/row)

Transaction model: psycopg3 manages the transaction through ``with conn:`` —
it BEGINs on the first execute, COMMITs on clean block exit, and ROLLBACKs on
exception. We deliberately do NOT issue a manual ``BEGIN ISOLATION LEVEL
SERIALIZABLE``: psycopg3 silently ignores a nested BEGIN, and the default
READ COMMITTED isolation is the correct level for a ``FOR UPDATE SKIP LOCKED``
queue consumer (SERIALIZABLE would add serialization-failure retries for no
benefit, since SKIP LOCKED already gives each invocation a disjoint row set).

Requires DATABASE_URL env var pointing to Supabase postgres://...

Return contract: :func:`advance_global_tasks` returns the number of event rows
inserted (>= 0) on success, or -1 on any error (missing config, connection
failure, transaction failure). The CLI maps -1 -> exit code 1 so Task Scheduler
flags failed runs instead of silently treating them as "0 events, success".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Max events emitted per fire_per_interval row in a single invocation. A row
# that has lapsed more intervals than this drains across successive invocations
# rather than flooding the queue in one tick.
FIRE_PER_INTERVAL_CAP = 24

# Dedup sentinel epoch for a never-run row (next_run IS NULL). A fixed value
# (rather than now()) keeps the first-run dedup_key stable across retries: if
# the advancer crashes after the INSERT but before the next_run UPDATE commits,
# the retry recomputes the same dedup_key and ON CONFLICT DO NOTHING dedupes it.
# A source uses this sentinel at most once (its first fire); every later fire
# has a concrete next_run.
NULL_NEXT_RUN_SENTINEL_EPOCH = 0.0


def digest_sha256(data: str) -> str:
    """SHA256 hex digest of a string (matching postgres digest(..., 'hex'))."""
    return hashlib.sha256(data.encode()).hexdigest()


def compute_dedup_key(source_id: str, next_run_epoch: float, mode: str = "coalesce") -> str:
    """Compute dedup_key = sha256('global_task:'||mode||':'||source_id||':'||epoch).

    ``mode`` ('coalesce' | 'fire') namespaces the key so a coalesce event and a
    fire_per_interval i=0 event for the same row do NOT collide (MAJOR #6):
    fire_epoch for i=0 is ``base_epoch + 0*cadence == base_epoch``, the exact
    epoch the coalesce branch uses, so without the mode prefix both hash to
    'global_task:<id>:<epoch>' and ``ON CONFLICT DO NOTHING`` silently swallows
    whichever lands second — a coalesce→fire_per_interval mode switch would lose
    its first fire. The prefix is mode-stable (it is a column value, not derived
    from clock state), so crash-retry of the same row in the same mode still
    recomputes the identical key and dedupes correctly.

    Args:
        source_id: UUID string
        next_run_epoch: seconds since epoch (full timestamp, NOT hour-truncated)
        mode: lapse mode namespace — 'coalesce' (default) or 'fire'

    Returns:
        Hex digest
    """
    data = f"global_task:{mode}:{source_id}:{int(next_run_epoch)}"
    return digest_sha256(data)


def _next_run_epoch(next_run: Any) -> float:
    """Epoch seconds used for dedup. Never-run rows (next_run IS NULL) use a
    fixed sentinel so their first-run dedup_key is stable across retries."""
    if next_run is None:
        return NULL_NEXT_RUN_SENTINEL_EPOCH
    return next_run.timestamp()


def _intervals_lapsed(cur: Any, next_run: Any, cadence: Any) -> int:
    """Cadence intervals elapsed since next_run, floored, +1 (>= 1).

    The interval arithmetic runs in Postgres so cadence's real unit (minutes /
    hours / days) is honoured rather than guessed at in Python.
    """
    cur.execute(
        """
        SELECT EXTRACT(EPOCH FROM (now() - %s::timestamptz)) /
               EXTRACT(EPOCH FROM %s::interval) AS intervals_missed
        """,
        (next_run, cadence),
    )
    intervals_missed = cur.fetchone()["intervals_missed"]
    return max(1, int(intervals_missed) + 1)


def _insert_event(cur: Any, *, title: str, event_payload: dict[str, Any], dedup_key: str) -> int:
    """INSERT one global_task_due event, deduped on dedup_key.

    Returns the number of rows ACTUALLY inserted: 1 on insert, 0 when ON CONFLICT
    DO NOTHING skips a duplicate. Callers must add this (not a blind +1) so the
    event count reflects real inserts.
    """
    cur.execute(
        """
        INSERT INTO events (
            event_type, severity, repo, source, title, payload, dedup_key
        ) VALUES (
            'global_task_due', 'low', '_global', 'global_task_advancer',
            %s, %s::jsonb, %s
        )
        ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
        """,
        (title, json.dumps(event_payload, default=str), dedup_key),
    )
    # rowcount: 1 on insert, 0 when ON CONFLICT DO NOTHING skipped a duplicate.
    return max(0, cur.rowcount)


def _advance_next_run(cur: Any, source_id: str, cadence: Any, periods: int) -> None:
    """Move a row's schedule forward.

    Recurring rows (cadence not NULL) advance next_run by ``periods`` cadence
    widths — coalesce passes 1, fire_per_interval passes its fire count so the
    row jumps past every interval it just fired (otherwise it stays "due" and
    re-selects every invocation until next_run finally crawls past now()).
    GREATEST(now(), ...) clamps a badly-lapsed row forward rather than leaving
    it perpetually behind. One-shot rows (cadence IS NULL) disable themselves.
    """
    if cadence is not None:
        cur.execute(
            """
            UPDATE global_task_sources
            SET last_run = now(),
                next_run = GREATEST(now(), coalesce(next_run, now()) + (%s * %s::interval))
            WHERE id = %s
            """,
            (periods, cadence, source_id),
        )
    else:
        cur.execute(
            """
            UPDATE global_task_sources
            SET last_run = now(),
                next_run = NULL,
                enabled = false
            WHERE id = %s
            """,
            (source_id,),
        )


def _advance_due_rows(cur: Any) -> int:
    """Core advancer logic against an open cursor (dict rows). Returns the count
    of event rows actually inserted.

    Separated from connection/transaction management so it is unit-testable with
    a fake cursor — no live Postgres required. The caller owns the transaction.
    """
    total_events = 0

    logger.info("Fetching due global_task_sources...")
    cur.execute(
        """
        SELECT
            id, title, body, dispatcher_skill, output_sink, payload,
            cadence, last_run, next_run, on_lapse
        FROM global_task_sources
        WHERE enabled = true
          AND (next_run IS NULL OR next_run <= now())
        ORDER BY created_at ASC
        FOR UPDATE SKIP LOCKED
        """
    )
    due_rows = cur.fetchall()
    logger.info(f"Found {len(due_rows)} due rows")

    for row in due_rows:
        source_id = str(row["id"])
        title = row["title"]
        body = row["body"]
        dispatcher_skill = row["dispatcher_skill"]
        output_sink = row["output_sink"]
        payload = row["payload"] or {}
        cadence = row["cadence"]
        next_run = row["next_run"]
        on_lapse = row["on_lapse"]

        # Belt-and-suspenders against a sub-1s cadence (the DB constraint
        # global_task_sources_cadence_floor should already reject these at
        # insert). _intervals_lapsed divides by EXTRACT(EPOCH FROM cadence), so a
        # zero cadence is a divide-by-zero that aborts the whole transaction —
        # one bad row would block every other due row in the same tick. A
        # sub-second cadence collides dedup_keys across ticks. Skip + log rather
        # than crash; the row stays due and is revisited once its cadence is
        # corrected.
        if cadence is not None and cadence.total_seconds() < 1.0:
            logger.error(
                f"Skipping global_task_source {source_id}: cadence "
                f"{cadence.total_seconds()}s is below the 1s floor "
                "(violates global_task_sources_cadence_floor)"
            )
            continue

        base_epoch = _next_run_epoch(next_run)

        if on_lapse == "fire_per_interval":
            # Fire once per lapsed interval, bounded by the cap.
            if next_run is None or cadence is None:
                # First run or one-shot: a single fire at the base epoch. There is
                # no cadence to step subsequent fires by, and fire_count == 1 means
                # the loop below only ever uses i == 0 (fire_epoch == base_epoch),
                # so cadence_seconds is never read as a real offset here.
                fire_count = 1
                cadence_seconds = 0.0
            else:
                fire_count = min(FIRE_PER_INTERVAL_CAP, _intervals_lapsed(cur, next_run, cadence))
                cadence_seconds = cadence.total_seconds()

            if fire_count >= FIRE_PER_INTERVAL_CAP:
                logger.warning(
                    f"fire_per_interval cap reached for {source_id} "
                    f"({fire_count} fires); remaining lapse drains next invocation"
                )

            for i in range(fire_count):
                fire_epoch = base_epoch + i * cadence_seconds
                dedup_key = compute_dedup_key(source_id, fire_epoch, "fire")
                event_payload = {
                    "source_id": source_id,
                    "title": title,
                    "body": body,
                    "dispatcher_skill": dispatcher_skill,
                    "output_sink": output_sink,
                    "payload": payload,
                    "lapse_intervals": i + 1,
                }
                total_events += _insert_event(
                    cur, title=title, event_payload=event_payload, dedup_key=dedup_key
                )

            _advance_next_run(cur, source_id, cadence, fire_count)

        else:
            # coalesce (default): one event regardless of how many intervals lapsed.
            if next_run is None or cadence is None:
                lapse_intervals = 1
            else:
                lapse_intervals = _intervals_lapsed(cur, next_run, cadence)

            dedup_key = compute_dedup_key(source_id, base_epoch, "coalesce")
            event_payload = {
                "source_id": source_id,
                "title": title,
                "body": body,
                "dispatcher_skill": dispatcher_skill,
                "output_sink": output_sink,
                "payload": payload,
                "lapse_intervals": lapse_intervals,
            }
            logger.info(
                f"Inserting event for {source_id} "
                f"(lapse_intervals={lapse_intervals}, dedup_key={dedup_key[:8]}...)"
            )
            total_events += _insert_event(
                cur, title=title, event_payload=event_payload, dedup_key=dedup_key
            )

            _advance_next_run(cur, source_id, cadence, 1)

    return total_events


def advance_global_tasks(db_url: str | None = None) -> int:
    """Advance due global task sources to events queue.

    Returns:
        Number of event rows inserted (>= 0) on success, or -1 on error.
    """
    if not db_url:
        db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL env var not set")
        return -1

    try:
        conn = psycopg.connect(db_url, row_factory=dict_row)
    except Exception as e:
        # Do NOT log the exception payload: a psycopg connection error can echo
        # the DSN (which carries the password) into Task Scheduler logs.
        logger.error(f"Failed to connect to database: {type(e).__name__}")
        return -1

    try:
        # psycopg3: `with conn:` opens a transaction and COMMITs on clean exit /
        # ROLLBACKs on exception. No manual BEGIN, no manual commit.
        with conn:
            with conn.cursor() as cur:
                total_events = _advance_due_rows(cur)
        logger.info(f"Advanced global_task_sources: {total_events} events inserted")
        return total_events
    except Exception as e:
        # Symmetric with the connect handler above: log only the exception type,
        # never the payload. A psycopg error raised mid-transaction can echo the
        # failing statement and, in some adapters, connection context that
        # carries the DSN password into Task Scheduler logs.
        logger.error(f"advance_global_tasks transaction failed: {type(e).__name__}")
        return -1
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        count = advance_global_tasks()
        sys.exit(0 if count >= 0 else 1)
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        sys.exit(1)
