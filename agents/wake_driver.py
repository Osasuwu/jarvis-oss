"""wake_driver — crash-safe LISTEN/NOTIFY cold-boot loop (#743).

The wake_driver replaces the retired resident scheduler service
(``agents/scheduler.py``, #743). It is a
**program, not an agent**: it owns no decisions, only the wake mechanics —
"persistent BEHAVIOR, not a persistent PROCESS" (milestone #44, decisions
``efa255cc`` / ``2c5384d0``).

Behavior:

- ``LISTEN`` on the events NOTIFY channel (``'events'``, the
  ``notify_events_insert`` trigger from the #739 substrate). Each wake signal
  cold-boots the orchestrator for the next single ``pending`` event; the loop
  advances to the next as soon as the prior tick finishes — no cron, no fixed
  interval, no resident sleep-poll loop.
- **At-least-once.** An event stays ``claimed`` until the orchestrator commits
  ``processed``. A crash mid-tick leaves the row ``claimed``; a **watchdog**
  re-claims rows older than a threshold so a dead orchestrator never strands
  work. The watchdog also fires on the wait timeout, so it runs even when no
  NOTIFY arrives.
- The orchestrator is **injected** (an ``Orchestrator = Callable[[dict], Any]``).
  ``default_orchestrator`` (a trivial log-and-return stub) exists only for the
  pure-loop unit tests. ``main()`` (#1385) wires
  :func:`agents.orchestrator.build_production_orchestrator` instead — a
  closure over the real ``handle_event`` → ``dispatch`` router — via a
  function-local import, so the module itself still never imports
  ``agents.orchestrator`` at load time; the driver stays mechanics-only at
  import time, live routing is main()'s wiring choice, not a module coupling.
- **Path B (#745)** — the tick optionally runs the parked-event re-queue poller
  before draining, so events that were parked because their blocking task
  completed are re-queued to ``pending`` and picked up on the same tick.
- **Task completion loop (#921).** When a ``task_port`` is wired in, each tick
  also polls the processes spawned by earlier ticks (the in-memory liveness
  map owned by :func:`run`) and closes their ``task_queue`` rows: exit 0 →
  ``done``, non-zero → ``failed``. Model P semantics — ``done`` means *the
  spawned process exited cleanly*, nothing more; it is not task success and
  not PR-merged. Outcome truth re-enters via Path-A GitHub events. **Restart
  limitation:** the map is process-local, so a driver restart forgets every
  live process — those rows age out as orphans and the reaper backstop fails
  them (self-healing via Path A; a PID sidecar that survives restarts is #952).

The pure loop (:func:`drain_pending` / :func:`run_watchdog` / :func:`tick` /
:func:`run`) operates over an :class:`EventQueuePort`, so it is unit-testable
with an FSM-faithful fake. :class:`PsycopgEventQueue` is the real adapter over
the #739 Postgres RPCs and the LISTEN socket.

CLI::

    python -m agents.wake_driver                      # block on NOTIFY, drain forever
    python -m agents.wake_driver --watchdog-seconds 120
    python -m agents.wake_driver --once               # one tick (drain + watchdog) then exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any, Protocol

from dotenv import load_dotenv

from agents.config import load_config
from agents.pid_sidecar import Sidecar
from agents.task_dispatch import (
    DEFAULT_CLAIMED_STALE_SECONDS,
    DEFAULT_RUNNING_REAP_SECONDS,
    DEFAULT_WORKTREE_RETENTION_CAP,
    DEFAULT_WORKTREE_RETENTION_TTL_SECONDS,
    EventEmit,
    ReadUsage,
    ResolveBinary,
    Spawn,
    SupabaseTaskQueue,
    TaskQueuePort,
    TrackedProc,
    WorktreeSweepResult,
    default_read_usage,
    DedupConfig,
    default_resolve_binary,
    default_spawn,
    default_stdout_reader,
    default_task_dedup,
    drain_tasks,
    kill_process_tree,
    kill_runaways,
    poll_completions,
    reclaim_stale_tasks,
    sweep_task_worktrees,
)

# Module-level, not lazy-in-tick: agents.poller imports only stdlib, so there is
# no import cycle to defer around. The Path B poll step runs every tick when a
# poller_port is wired, so a per-call import bought nothing but obscurity.
from agents.poller import poll as poll_parked

if TYPE_CHECKING:
    import psycopg

    from agents.github_client import GitHubClient
    from agents.poller import PollerPort

logger = logging.getLogger(__name__)

# Re-claim a ``claimed`` row after this long with no ``processed`` commit. Also
# the wait-for-wake timeout, so the watchdog runs on an idle queue.
DEFAULT_STALE_AFTER_SECONDS = 300

# Identifies this driver in events.claimed_by for traceability.
CLAIMER = "wake_driver"

# Repo-root-anchored (not CWD-relative) so the log lands in the same place
# regardless of the Scheduled Task's working directory — same anchoring
# rationale as executor._STDERR_LOG_DIR.
_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "wake_driver"
)

# Cold-boot retry: AtLogOn can fire before the network/DNS/Supabase is
# reachable (observed: task started 07:31, died within ~5min after
# exhausting Task Scheduler's RestartCount=5/1-min budget, manual restart at
# 14:30 worked immediately — a boot-time race, not a code defect). Retrying
# in-process closes that race faster than five minute-apart process restarts,
# and produces one continuous log instead of five truncated ones.
_STARTUP_RETRY_ATTEMPTS = 5
_STARTUP_RETRY_BASE_SECONDS = 5.0
_STARTUP_RETRY_MAX_SECONDS = 60.0


def _configure_logging() -> None:
    """Console + rotating file logging.

    Task Scheduler does not capture stdout/stderr for an AtLogOn task, so a
    boot-time crash previously left no trace beyond the exit code. A file
    handler under ``logs/wake_driver/`` survives that gap. Falls back to
    console-only if the log directory can't be created (e.g. permissions) —
    a logging setup failure must never be the reason startup dies silently.
    """
    if logging.getLogger().hasHandlers():
        return  # already configured — e.g. main() invoked more than once in-process
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                os.path.join(_LOG_DIR, "wake_driver.log"),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    except OSError as exc:
        print(f"[wake_driver] file logging unavailable ({exc}); console only", file=sys.stderr)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers
    )


def _call_with_retry(
    build: Callable[[], Any],
    *,
    what: str,
    attempts: int = _STARTUP_RETRY_ATTEMPTS,
    base_delay: float = _STARTUP_RETRY_BASE_SECONDS,
    max_delay: float = _STARTUP_RETRY_MAX_SECONDS,
) -> Any:
    """Retry a cold-boot connection builder with exponential backoff.

    Transient — DNS/network/Supabase not yet reachable seconds after boot.
    Not a general-purpose retry: only for the one-shot builders called during
    startup, before the loop is otherwise live.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return build()
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "[wake_driver] startup: %s failed (attempt %d/%d): %s — retrying in %.0fs",
                what,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    logger.error("[wake_driver] startup: %s failed after %d attempts, giving up", what, attempts)
    assert last_exc is not None
    raise last_exc


# The NOTIFY channel from the #739 substrate (notify_events_insert).
EVENTS_CHANNEL = "events"

# The NOTIFY channel from the #922 task_queue substrate (notify_task_queue_insert).
# Fires when a task row reaches ``pending`` after a cap-freed transition or fresh
# insert, waking the driver to drain without waiting for the idle timeout.
TASK_QUEUE_CHANNEL = "task_queue"

# Cap on one poller sweep's parked-event fetch (#1475 review, MEDIUM — the M2
# finding PR #964 deferred until the production adapter shipped). Nothing in
# production writes ``blocked_by_task_id`` yet (#1455), so this is a ceiling
# on a currently-empty query, not a tuned value — revisit once #1455 ships a
# producer and real volume is observable.
# ceiling: unbounded parked-event backlog beyond this count is left for the
# next sweep rather than fetched in one pass; raise once #1455's producer
# ships and real backlog sizes are observable.
PARKED_EVENTS_LIMIT = 200

# Orchestrator stub returns whatever it likes; the driver only cares that it
# returned without raising before committing ``processed``.
Orchestrator = Callable[[dict[str, Any]], Any]


class EventQueuePort(Protocol):
    """The slice of the events FSM the loop depends on.

    Implemented for real by :class:`PsycopgEventQueue` over the #739 RPCs,
    and by an in-memory fake in the tests.
    """

    def claim_next(self) -> dict[str, Any] | None:
        """Claim the highest-severity ``pending`` event (pending→claimed)."""

    def mark_processed(self, event_id: str, *, action: str = "") -> bool:
        """Commit a ``claimed`` event to ``processed``."""

    def park(self, event_id: str, *, reason: str = "") -> bool:
        """Commit a ``claimed`` event to ``parked`` (#1385 AC-C poison-pill).

        A parked event carries no ``blocked_by_task_id`` in its payload, so
        :mod:`agents.poller` (Path B, #745) never re-queues it — a
        crash-parked event stays parked until a human intervenes. Wiring a
        live requeue path for this case is out of scope (#1393).
        """

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        """Return ``claimed`` rows older than the threshold to ``pending``."""

    def wait_for_wake(self, *, timeout_seconds: float | None) -> bool:
        """Block until a NOTIFY arrives or the timeout elapses.

        Returns ``True`` on a wake signal, ``False`` on timeout.
        """

    def recent_events(self, *, limit: int) -> list[dict[str, Any]]:
        """Return the ``limit`` most-recently-created events, read-only.

        No claim, no state change (#1385 AC-E Stage 1 ``--dry-run``) — a
        plain ``SELECT`` so a preview never competes with the live loop for
        the same rows.
        """


@dataclass(frozen=True)
class TickResult:
    """What one :func:`tick` did — completion poll, watchdogs, then both drains.

    ``requeued`` counts ``parked`` events the Path B poller (#745) returned to
    ``pending`` this tick. The ``tasks_*`` fields default to 0 so an event-only
    tick (no ``task_port``) constructs unchanged. ``tasks_done`` /
    ``tasks_failed_exit`` count rows closed by the #921 completion poll (Model P:
    *done* = process exited 0, nothing more — not task success, not PR merged).
    Runaways tree-killed by the same step fold into ``tasks_failed_exit`` (their
    rows end ``failed`` just like a non-zero exit).
    """

    reclaimed: int
    processed: int
    requeued: int = 0
    tasks_reclaimed: int = 0
    tasks_reaped: int = 0
    tasks_spawned: int = 0
    tasks_failed: int = 0
    tasks_done: int = 0
    tasks_failed_exit: int = 0
    worktrees_pruned: int = 0
    worktrees_retained: int = 0
    worktrees_ttl_pruned: int = 0
    worktrees_cap_evicted: int = 0


def default_orchestrator(event: dict[str, Any]) -> None:
    """Trivial stub orchestrator for this slice — log and return.

    The driver commits ``processed`` after this returns. The real
    local-model router (qwen3 on Workshop) is wired in a later slice and is
    injected, never imported here.
    """
    logger.info(
        "[wake_driver] stub-processing event id=%s type=%s severity=%s",
        event.get("id"),
        event.get("event_type"),
        event.get("severity"),
    )


def drain_pending(
    port: EventQueuePort,
    orchestrator: Orchestrator,
    *,
    failed_events: dict[str, int] | None = None,
) -> int:
    """Drain every ``pending`` event, one at a time, until the queue is empty.

    Claims, hands the event to ``orchestrator``, then commits ``processed`` —
    advancing to the next event as soon as the prior finishes (no interval).

    Crash-safety (attempt 1): if ``orchestrator`` raises on an event id seen
    for the first time in ``failed_events``, the exception propagates and
    ``mark_processed`` is **not** reached — the event is left ``claimed``
    (recoverable by the watchdog) rather than lost or marked done, exactly as
    a process kill would leave it.

    Poison-pill handling (attempt 2, #1385 AC-C): if the *same* event id
    raises again — meaning a prior ``drain_pending`` call already recorded
    one failure for it in the same ``failed_events`` mapping (``run`` owns
    one such mapping across the whole loop; see its docstring) — the event is
    parked instead of re-raised, and the drain continues to the next event.
    This distinguishes a transient infra failure (attempt 1: abort, let the
    watchdog retry) from a permanently-broken event (attempt 2: stop retrying
    forever, park it for a human).

    ``failed_events`` defaults to a fresh, call-local dict when omitted —
    each isolated call then behaves as if poison-pill tracking didn't exist
    (a first failure always re-raises), which is what every pre-#1385 caller
    (and test) expects.
    """
    attempts = failed_events if failed_events is not None else {}
    processed = 0
    while (event := port.claim_next()) is not None:
        event_id = str(event["id"])
        try:
            orchestrator(event)
        except Exception as exc:
            attempts[event_id] = attempts.get(event_id, 0) + 1
            if attempts[event_id] < 2:
                raise
            port.park(event_id, reason=f"poison-pill: {type(exc).__name__}: {exc}")
            continue
        port.mark_processed(event_id)
        processed += 1
    return processed


def run_watchdog(port: EventQueuePort, *, stale_after_seconds: float) -> int:
    """Re-claim events stranded in ``claimed`` past the threshold.

    Returns the number of rows returned to ``pending``.
    """
    reclaimed = port.reclaim_stale(older_than_seconds=stale_after_seconds)
    if reclaimed:
        logger.info("[wake_driver] watchdog re-claimed %d stale event(s)", reclaimed)
    return reclaimed


def tick(
    port: EventQueuePort,
    orchestrator: Orchestrator,
    *,
    stale_after_seconds: float,
    poller_port: PollerPort | None = None,
    task_port: TaskQueuePort | None = None,
    task_spawn: Spawn = default_spawn,
    task_resolve_binary: ResolveBinary = default_resolve_binary,
    task_read_usage: ReadUsage = default_read_usage,
    task_claimed_stale_after_seconds: float = DEFAULT_CLAIMED_STALE_SECONDS,
    task_running_reap_after_seconds: float = DEFAULT_RUNNING_REAP_SECONDS,
    task_procs: dict[str, TrackedProc] | None = None,
    task_clock: Callable[[], float] = time.monotonic,
    task_kill: Callable[[Any], None] = kill_process_tree,
    task_sidecar: Sidecar | None = None,
    task_event_emit: EventEmit | None = None,
    task_evidence_client: GitHubClient | None = None,
    task_stdout_reader: Callable[[str], str | None] | None = None,
    task_dedup: DedupConfig | None = None,
    task_worktree_retention_seconds: float = DEFAULT_WORKTREE_RETENTION_TTL_SECONDS,
    task_worktree_retention_cap: int = DEFAULT_WORKTREE_RETENTION_CAP,
    task_worktree_now: Callable[[], float] = time.time,
    failed_events: dict[str, int] | None = None,
) -> TickResult:
    """One unit of work — ordered steps (#909 AC1, #921 AC3, #745 Path B, #1390 AC6)::

        poll_completions() + kill_runaways()                  # Step 0, #921
        → reclaim_stale(events)                               # Step 1, event watchdog
        → reclaim_stale_tasks()                               # Step 2, task watchdog
        → sweep_task_worktrees()                              # Step 2a, #1390 AC6
        → poll(parked events)                                 # Step 2b, Path B #745
        → drain_pending(events)                               # Step 3, event drain
        → drain_tasks()                                       # Step 4, task drain

    Step 0 closes ``running`` rows whose tracked process exited (rc 0 → done,
    rc ≠0 → failed) and tree-kills live processes past the reap threshold —
    *before* anything else, so freed cap slots are visible to this same tick's
    drain and freshly-closed rows are no longer ``running`` when the orphan
    reaper scans. It runs only when ``task_procs`` (the cross-tick liveness
    map, owned by :func:`run`) is supplied; ``--once`` and event-only ticks
    skip it.

    The task watchdog receives the map's keyset as ``live_task_ids`` (#921
    AC5): rows with a live tracked process are never time-reaped, however old —
    a fresh driver (empty/absent map) treats every stale running row as an
    orphan again, which is the documented restart limitation (the map does not
    survive restart; Path-A re-drives the lost work; PID sidecar = #952).

    After the drain, each spawned ``(task_id, proc)`` pair is folded into
    ``task_procs`` stamped with ``task_clock`` so a later tick can close it.

    Both watchdogs run **before** both drains, so a row stranded by a previous
    crash (event *or* task) is returned to ``pending`` and re-driven within the
    same tick. Tasks are swept and drained only when ``task_port`` is supplied;
    omitting it preserves the original event-only behavior. There is no task
    NOTIFY — a task is born from an event that already woke the driver, or is
    swept by the idle-timeout watchdog (AC1; task-NOTIFY latency deferred to
    #922).

    ``failed_events`` is the cross-tick poison-pill attempt counter for Step 3
    (#1385 AC-C) — see :func:`drain_pending` and :func:`run`, which owns the
    mapping across the whole loop.

    The task steps (0, 2, 2a and 4) are each isolated in their own try/except:
    the task_queue rides supabase-py while events ride psycopg, so a
    task-store outage is an independent failure mode. It must not block the
    event drain (Step 3) — events are the primary wake path. A failing task
    step is logged and its rows stay in place (``claimed``/``running`` →
    swept next tick; ``pending`` → re-drained next tick), exactly as a crash
    would leave them.

    Step 2a (#1390 AC6) prunes on-disk task worktrees whose task row is
    absent or terminal-non-failure, TTLs and count-caps the ones retained
    for a failed task's retry (AC5), and runs ``git worktree prune`` so
    git's own registration stays in sync with the filesystem. It runs after
    the task watchdog (Step 2) so a row Step 2 just reaped to ``failed``
    this same tick is already sweep-eligible, and before the event drain
    (Step 3) so a stuck removal never delays the primary wake path.
    """
    # Step 0 — completion poll + runaway kill (#921 AC2/AC3/AC6). Two
    # independent halves: a completion-poll blowup must not stop the runaway
    # killer from bounding live processes, so each gets its own isolation.
    completions = None
    runaways_killed = 0
    if task_port is not None and task_procs is not None:
        try:
            completions = poll_completions(
                task_port,
                task_procs,
                sidecar=task_sidecar,
                event_emit=task_event_emit,
                evidence_client=task_evidence_client,
                stdout_reader=task_stdout_reader,
            )
        except Exception:  # noqa: BLE001 — task-store outage must not block event drain
            logger.exception("[wake_driver] completion poll failed; tracked rows retry next tick")
        try:
            runaways_killed = kill_runaways(
                task_port,
                task_procs,
                max_runtime_seconds=task_running_reap_after_seconds,
                now=task_clock,
                kill=task_kill,
                sidecar=task_sidecar,
                event_emit=task_event_emit,
            )
        except Exception:  # noqa: BLE001 — same isolation for the runaway killer
            logger.exception("[wake_driver] runaway kill failed; live rows retry next tick")

    # Step 1 — event watchdog.
    reclaimed = run_watchdog(port, stale_after_seconds=stale_after_seconds)

    # Step 2 — task watchdog (stale claimed → pending, orphaned running → failed).
    task_reclaim = None
    if task_port is not None:
        try:
            task_reclaim = reclaim_stale_tasks(
                task_port,
                claimed_stale_after_seconds=task_claimed_stale_after_seconds,
                running_reap_after_seconds=task_running_reap_after_seconds,
                live_task_ids=frozenset(task_procs or ()),
            )
        except Exception:  # noqa: BLE001 — task-store outage must not block event drain
            logger.exception(
                "[wake_driver] task watchdog failed; stale task rows left for the next tick"
            )

    # Step 2a — task worktree sweep (#1390 AC6). Runs after the task watchdog
    # (Step 2) so an orphaned row it just reaped to `failed` this tick becomes
    # sweep-eligible immediately rather than waiting for the next tick.
    worktree_sweep: WorktreeSweepResult | None = None
    if task_port is not None:
        try:
            worktree_sweep = sweep_task_worktrees(
                task_port,
                retention_seconds=task_worktree_retention_seconds,
                retention_cap=task_worktree_retention_cap,
                now=task_worktree_now,
            )
        except Exception:  # noqa: BLE001 — task-store/git outage must not block event drain
            logger.exception(
                "[wake_driver] worktree sweep failed; stale worktrees left for the next tick"
            )

    # Step 2b — Path B parked-event re-queue (#745). Runs after the completion
    # poll (Step 0) has closed done/failed task rows and before the event drain,
    # so an event whose blocking task just finished is re-queued to ``pending``
    # and drained in this same tick rather than waiting for the next wake.
    requeued = 0
    if poller_port is not None:
        # Isolated like the task steps (0/2/4): a poller outage must not skip the
        # event drain (Step 3). Without this guard a single poll() raise would
        # propagate out of tick(), strand every event claimed earlier this pass,
        # and bypass drain_pending entirely — the primary wake path.
        try:
            requeued = poll_parked(poller_port)
        except Exception:  # noqa: BLE001 — poller outage must not block the event drain
            logger.exception(
                "[wake_driver] parked-event poller failed; parked events retry next tick"
            )

    # Step 3 — event drain.
    processed = drain_pending(port, orchestrator, failed_events=failed_events)

    # Step 4 — task drain (claim → running → spawn, capped, Ordering B), then
    # fold the new handles into the liveness map for later ticks to close.
    task_drain = None
    if task_port is not None:
        try:
            # Stamp BEFORE the drain: a broken clock then fails the step while
            # no process exists yet — stamped after, the raise would discard
            # the just-spawned handles (orphans for the 6h reaper).
            started = task_clock()
            task_drain = drain_tasks(
                task_port,
                task_spawn,
                resolve_binary=task_resolve_binary,
                read_usage=task_read_usage,
                sidecar=task_sidecar,
                dedup=task_dedup,
            )
            if task_procs is not None:
                for task_id, proc in task_drain.procs:
                    # Enrich with the spawn metadata (#953): goal +
                    # idempotency_key + tz-aware spawned_at are what the next
                    # tick's completion poll needs to compute PR evidence and
                    # emit a terminal event. Adopted-after-restart procs carry no
                    # meta → empty goal → null evidence → escalate (documented).
                    meta = task_drain.spawned_meta.get(task_id, {})
                    task_procs[task_id] = TrackedProc(
                        proc=proc,
                        started_at=started,
                        goal=meta.get("goal", ""),
                        idempotency_key=meta.get("idempotency_key", ""),
                        spawned_at=meta.get("spawned_at"),
                    )
        except Exception:  # noqa: BLE001 — task-store outage must not crash the tick
            logger.exception(
                "[wake_driver] task drain failed; pending tasks left for the next tick"
            )

    return TickResult(
        reclaimed=reclaimed,
        processed=processed,
        requeued=requeued,
        tasks_reclaimed=task_reclaim.reclaimed_claimed if task_reclaim else 0,
        tasks_reaped=task_reclaim.reaped_running if task_reclaim else 0,
        tasks_spawned=task_drain.spawned if task_drain else 0,
        tasks_failed=task_drain.failed if task_drain else 0,
        tasks_done=completions.done if completions else 0,
        tasks_failed_exit=(completions.failed_exit if completions else 0) + runaways_killed,
        worktrees_pruned=worktree_sweep.pruned if worktree_sweep else 0,
        worktrees_retained=worktree_sweep.retained if worktree_sweep else 0,
        worktrees_ttl_pruned=worktree_sweep.ttl_pruned if worktree_sweep else 0,
        worktrees_cap_evicted=worktree_sweep.cap_evicted if worktree_sweep else 0,
    )


def run(
    port: EventQueuePort,
    orchestrator: Orchestrator = default_orchestrator,
    *,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    should_continue: Callable[[], bool] | None = None,
    poller_port: PollerPort | None = None,
    task_port: TaskQueuePort | None = None,
    task_spawn: Spawn = default_spawn,
    task_resolve_binary: ResolveBinary = default_resolve_binary,
    task_read_usage: ReadUsage = default_read_usage,
    task_claimed_stale_after_seconds: float = DEFAULT_CLAIMED_STALE_SECONDS,
    task_running_reap_after_seconds: float = DEFAULT_RUNNING_REAP_SECONDS,
    task_procs: dict[str, TrackedProc] | None = None,
    task_clock: Callable[[], float] = time.monotonic,
    task_kill: Callable[[Any], None] = kill_process_tree,
    task_event_emit: EventEmit | None = None,
    task_evidence_client: GitHubClient | None = None,
    task_stdout_reader: Callable[[str], str | None] | None = None,
    task_dedup: DedupConfig | None = None,
    task_worktree_retention_seconds: float = DEFAULT_WORKTREE_RETENTION_TTL_SECONDS,
    task_worktree_retention_cap: int = DEFAULT_WORKTREE_RETENTION_CAP,
    task_worktree_now: Callable[[], float] = time.time,
) -> None:
    """The event-driven loop: block on a wake signal, then run one tick.

    The loop blocks on :meth:`EventQueuePort.wait_for_wake` (the NOTIFY
    socket in the real adapter) with the watchdog interval as its timeout —
    so a NOTIFY *or* an idle timeout both drive a :func:`tick`. There is no
    busy sleep-poll; ``should_continue`` (default: forever) lets tests bound
    the loop.

    When ``poller_port`` is provided, each tick also re-queues ``parked``
    events whose blocking task has completed (Path B, #745).

    When ``task_port`` is supplied, each tick also sweeps and drains the
    ``task_queue`` (#909) — including the on-disk task-worktree sweep (#1390
    AC6) — and the loop owns the **liveness map** (#921): one
    ``{task_id: TrackedProc}`` dict created here (or injected via
    ``task_procs``) and handed to every tick, so a process spawned in tick N
    is polled to completion in tick N+M. The map lives only in this process —
    a restart loses it, stale rows become orphans, and the reaper backstop
    fails them (documented #921 AC7 limitation; PID sidecar = #952). The
    ``task_*`` knobs are forwarded to each :func:`tick` so spawn, resolver,
    quota probe, thresholds, clock, and killer stay injectable end-to-end
    (tests and operators), not just at the ``tick`` boundary.

    A tick that raises is logged and swallowed so a transient failure does
    not tear down the driver — the offending event stays ``claimed`` and the
    watchdog re-claims it next pass (at-least-once, never silently lost).

    ``run`` owns ``failed_events``, the poison-pill attempt counter (#1385
    AC-C): one ``{event_id: attempt_count}`` dict created here and handed to
    every :func:`tick` call, so a second failure on the same event id — even
    across a watchdog reclaim between ticks — parks it instead of retrying
    forever. ``ceiling:`` the map is in-process only and resets on restart,
    same limitation as the ``task_procs`` liveness map above; an event that
    poisoned a prior process life gets one more retry after a restart, which
    is acceptable (restarts are rare; an infinite in-process retry loop is
    the failure mode this exists to prevent).
    """
    keep_going = should_continue or (lambda: True)
    procs = task_procs if task_procs is not None else ({} if task_port is not None else None)
    failed_events: dict[str, int] = {}

    # AC3 (#952) — boot adoption: re-adopt live processes from the sidecar directory.
    # Only in resident mode (task_port supplied, procs map exists).
    if task_port is not None and procs is not None and should_continue is None:
        try:
            sidecar = Sidecar()
            for task_id, proc in sidecar.adopt_live_processes():
                procs[task_id] = TrackedProc(proc=proc, started_at=task_clock())
        except Exception:  # noqa: BLE001 — boot adoption failure is non-fatal
            logger.exception("[wake_driver] boot adoption failed; will treat all rows as orphans")
            sidecar = None
    else:
        sidecar = None

    while keep_going():
        port.wait_for_wake(timeout_seconds=stale_after_seconds)
        try:
            tick(
                port,
                orchestrator,
                stale_after_seconds=stale_after_seconds,
                poller_port=poller_port,
                task_port=task_port,
                task_spawn=task_spawn,
                task_resolve_binary=task_resolve_binary,
                task_read_usage=task_read_usage,
                task_claimed_stale_after_seconds=task_claimed_stale_after_seconds,
                task_running_reap_after_seconds=task_running_reap_after_seconds,
                task_procs=procs,
                task_clock=task_clock,
                task_kill=task_kill,
                task_sidecar=sidecar,
                task_event_emit=task_event_emit,
                task_evidence_client=task_evidence_client,
                task_stdout_reader=task_stdout_reader,
                task_dedup=task_dedup,
                task_worktree_retention_seconds=task_worktree_retention_seconds,
                task_worktree_retention_cap=task_worktree_retention_cap,
                task_worktree_now=task_worktree_now,
                failed_events=failed_events,
            )
        except Exception:  # noqa: BLE001 — daemon must survive a bad tick
            logger.exception("[wake_driver] tick failed; event left claimed for watchdog re-claim")


class PsycopgEventQueue:
    """Real :class:`EventQueuePort` over the #739 Postgres substrate.

    Uses the migration RPCs (``claim_next`` / ``mark_processed``) and a
    direct ``UPDATE`` for the watchdog reclaim, and ``LISTEN`` on the
    ``events`` channel for the wake signal. Requires a psycopg connection —
    PostgREST (supabase-py) cannot ``LISTEN``, so this is the one place the
    agents reach Postgres directly.

    The RPC methods need a live DB and are not unit-tested; the constructor's
    LISTEN wiring is (a recording conn, no DB). Kept thin so the tested loop
    above carries the logic.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        claimer: str = CLAIMER,
        task_queue: SupabaseTaskQueue | None = None,
    ) -> None:
        self._conn = conn
        self._claimer = claimer
        # Defaults to an unshared instance so ad-hoc construction still works
        # (each call then resolves its own Supabase client lazily); a
        # long-running caller should build one client and inject it here —
        # same MAJOR fix as PR #1011's event client (finding #1, PR #1475
        # review).
        self._task_queue = task_queue if task_queue is not None else SupabaseTaskQueue()
        self._conn.execute(f"LISTEN {EVENTS_CHANNEL}")
        self._conn.execute(f"LISTEN {TASK_QUEUE_CHANNEL}")
        self._conn.commit()

    def claim_next(self) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM claim_next(%s)", (self._claimer,))
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d.name for d in cur.description]
        self._conn.commit()
        return dict(zip(cols, row, strict=True))

    def mark_processed(self, event_id: str, *, action: str = "") -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT mark_processed(%s, %s, %s)", (event_id, self._claimer, action))
            ok = bool(cur.fetchone()[0])
        self._conn.commit()
        return ok

    def park(self, event_id: str, *, reason: str = "") -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT park_event(%s, %s)", (event_id, reason))
            ok = bool(cur.fetchone()[0])
        self._conn.commit()
        return ok

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE events SET state = 'pending', claimed_at = NULL, claimed_by = NULL "
                "WHERE state = 'claimed' "
                "AND claimed_at < now() - make_interval(secs => %s) "
                "RETURNING id",
                (older_than_seconds,),
            )
            count = len(cur.fetchall())
        self._conn.commit()
        return count

    def wait_for_wake(self, *, timeout_seconds: float | None) -> bool:
        for _notify in self._conn.notifies(timeout=timeout_seconds, stop_after=1):
            return True
        return False

    def recent_events(self, *, limit: int) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
        self._conn.commit()
        return [dict(zip(cols, row, strict=True)) for row in rows]

    # -- PollerPort surface (#1393 — Path B consumer) ------------------------

    def find_parked_events(self) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM events WHERE state = 'parked' AND payload ? 'blocked_by_task_id' "
                "LIMIT %s",
                (PARKED_EVENTS_LIMIT,),
            )
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
        self._conn.commit()
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def get_task_statuses(self, task_ids: list[str]) -> dict[str, str]:
        return self._task_queue.get_statuses(task_ids)

    def requeue_event(self, event_id: str, *, reason: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT requeue_event(%s, %s)", (event_id, reason))
            ok = bool(cur.fetchone()[0])
        self._conn.commit()
        return ok


def dry_run(
    events: list[dict[str, Any]],
    handle_event: Callable[[dict[str, Any]], Any],
) -> list[Any]:
    """Stage 1 of the #1385 AC-E staged rollout — pure preview, no side effects.

    Maps ``handle_event`` (the deterministic router alone, not the
    ``dispatch``-wrapping production orchestrator) over a batch of events.
    Deliberately does not call :func:`drain_pending` — that claims and marks
    events processed; this only reads and routes in memory.
    """
    return [handle_event(event) for event in events]


def _print_dry_run_table(events: list[dict[str, Any]], decisions: list[Any]) -> None:
    """Render the Stage 1 preview as a columnar table on stdout.

    ``print`` (not ``logger``) — this is direct CLI output for a human
    running ``--dry-run``, not an operational log line.
    """
    header = f"{'event_type':<30} {'severity':<10} {'route':<20} {'reason'}"
    print(header)
    print("-" * len(header))
    escalate_count = 0
    for event, decision in zip(events, decisions, strict=True):
        route = getattr(decision, "route", None)
        route_name = getattr(route, "name", str(route))
        if route_name == "ESCALATE":
            escalate_count += 1
        reason = getattr(decision, "escalated_reason", None) or ""
        print(
            f"{event.get('event_type', '?'):<30} "
            f"{event.get('severity', '?'):<10} "
            f"{route_name:<20} "
            f"{reason}"
        )
    print(f"\n{len(events)} events previewed, {escalate_count} would ESCALATE")


def _build_psycopg_queue(*, task_queue: SupabaseTaskQueue | None = None) -> PsycopgEventQueue:
    cfg = load_config()
    if not cfg.postgres_url:
        raise RuntimeError(
            "AGENTS_POSTGRES_URL is not set. wake_driver needs a direct-Postgres "
            "session-mode DSN to open the LISTEN/NOTIFY socket — the PostgREST "
            "client can't LISTEN. Point it at db.<ref>.supabase.co:5432 or a "
            "session-pooler :5432 endpoint; never the transaction pooler :6543 "
            "(transaction mode drops LISTEN). See .env.example."
        )
    import psycopg

    conn = psycopg.connect(cfg.postgres_url, autocommit=False)
    return PsycopgEventQueue(conn, task_queue=task_queue)


def _default_event_emit(*, repo: str, client: Any | None = None) -> EventEmit:
    """Build the production terminal-event emitter (#953 AC1).

    Returns an ``emit(event_type, severity, payload, *, dedup_key=None)``
    closure that inserts into the ``events`` FSM table via
    :func:`agents.supabase_client.store_event` — the same inbox the driver
    drains, so a re-driven task is born from a real, dedup-keyed row. The
    title is derived from the payload's ``task_id`` for at-a-glance triage;
    everything else (lineage_key, attempt, pr_evidence, goal) rides in the
    ``payload``. ``store_event`` is imported lazily so a missing Supabase
    config doesn't break ``import wake_driver`` for the event-only path.
    """

    def emit(
        event_type: str,
        severity: str,
        payload: dict[str, Any],
        *,
        dedup_key: str | None = None,
    ) -> Any:
        from agents.supabase_client import store_event

        task_id = payload.get("task_id", "?")
        return store_event(
            event_type=event_type,
            repo=repo,
            title=f"{event_type}: task {task_id}",
            severity=severity,
            payload=payload,
            dedup_key=dedup_key,
            client=client,
        )

    return emit


def main() -> int:
    _configure_logging()
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watchdog-seconds",
        type=int,
        default=DEFAULT_STALE_AFTER_SECONDS,
        help=(
            "Re-claim claimed rows older than this many seconds; also the "
            f"wait-for-wake timeout (default: {DEFAULT_STALE_AFTER_SECONDS})"
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick (watchdog + drain) and exit (smoke test).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "#1385 AC-E Stage 1: read-only preview — route the most recent "
            "events through handle_event and print the decisions, no claim, "
            "no side effect. Exits immediately after printing."
        ),
    )
    parser.add_argument(
        "--dry-run-limit",
        type=int,
        default=200,
        help="Number of recent events to preview under --dry-run (default: 200).",
    )
    parser.add_argument(
        "--no-task-drain",
        action="store_true",
        help=(
            "#1385 AC-E Stage 2: run with task_port=None — tick's task "
            "completion-poll/watchdog/drain steps are skipped, so routing "
            "and escalation stay live but zero `claude -p` is spawned. "
            "Permanent flag, not temporary rollout scaffolding."
        ),
    )
    args = parser.parse_args()

    if args.dry_run:
        # Deliberately does not build SupabaseTaskQueue / the evidence client /
        # the Supabase event client / build_production_orchestrator — none of
        # that live wiring is needed for a read-only preview, and constructing
        # it would defeat the "no side effect" guarantee this stage exists for.
        queue = _call_with_retry(_build_psycopg_queue, what="psycopg event-queue connect")
        from agents.orchestrator import handle_event

        events = queue.recent_events(limit=args.dry_run_limit)
        decisions = dry_run(events, handle_event)
        _print_dry_run_table(events, decisions)
        return 0

    # Build the Supabase client ONCE and share it everywhere a long-running
    # driver would otherwise construct one per call/event (MAJOR, PR #1011;
    # extended to the task-queue side per #1475 review finding #1 — both
    # SupabaseTaskQueue() at the old task_port site below and
    # PsycopgEventQueue.get_task_statuses used to build their own unshared
    # instance, each triggering a fresh create_client() per invocation).
    from agents.supabase_client import get_client

    event_client = get_client()
    shared_task_queue = SupabaseTaskQueue(client=event_client)

    queue = _call_with_retry(
        lambda: _build_psycopg_queue(task_queue=shared_task_queue),
        what="psycopg event-queue connect",
    )
    # tasks ride supabase-py; events ride psycopg. --no-task-drain (Stage 2)
    # passes None so tick's task steps 0/2/4 skip (each already gated on
    # `task_port is not None`) — routing/escalation stay live, spawn doesn't.
    task_port = None if args.no_task_drain else shared_task_queue

    # #953 — evidence + terminal-event emission wiring. The repo scopes both the
    # GitHub evidence client (PR lookups) and the emitted events; the stdout
    # reader recovers a claimed PR number from the executor's JSON log when the
    # fresh-shape branch lookup comes up empty (AC3).
    from agents.github_client import default_github_client

    repo = os.environ.get("GITHUB_REPO", "your-username/your-repo")
    evidence_client = default_github_client()
    event_emit = _default_event_emit(repo=repo, client=event_client)

    # #1385 — live routing. The stub only logged; this closes handle_event's
    # Decision over dispatch's side effects (task_queue enqueue / escalation),
    # sharing the same lifetime Supabase client built above.
    from agents.notify import telegram_notifier
    from agents.orchestrator import build_production_orchestrator

    orchestrator = build_production_orchestrator(client=event_client, notifier=telegram_notifier)

    if args.once:
        # Deliberately no task_procs: a one-shot tick has no map from a prior
        # tick to poll, so completion-poll/runaway-kill are skipped and the
        # orphan reaper sees an empty live set — i.e. the #921 restart
        # semantics (stale running rows fail via the backstop).
        #
        # CONSTRAINT (#953): because completion-poll is skipped, the one-shot
        # path NEVER computes PR evidence nor emits task_done/task_failed events
        # — the evidence_client / event_emit / stdout_reader wiring above is
        # intentionally unused here. ``--once`` is a drain/watchdog smoke test
        # only; the #953 detection→emission path lives exclusively in the
        # long-running ``run(...)`` loop below. Do not "fix" this by threading
        # the wiring in — there is no prior-tick proc map for it to act on.
        try:
            result = tick(
                queue,
                orchestrator,
                stale_after_seconds=args.watchdog_seconds,
                task_port=task_port,
                poller_port=queue,
            )
            logger.info(
                "[wake_driver] one-shot tick: reclaimed=%d processed=%d requeued=%d "
                "tasks_reclaimed=%d tasks_reaped=%d tasks_spawned=%d tasks_failed=%d",
                result.reclaimed,
                result.processed,
                result.requeued,
                result.tasks_reclaimed,
                result.tasks_reaped,
                result.tasks_spawned,
                result.tasks_failed,
            )
        finally:
            # The one-shot path builds the lifetime evidence client (unused
            # here, see the CONSTRAINT note above) but still owns its pooled
            # sockets; release them before the short-circuit return so --once
            # doesn't leak an FD (L1, #1029). close() is idempotent.
            evidence_client.close()
        return 0

    logger.info(
        "[wake_driver] listening on '%s' channel (watchdog=%ss, Ctrl-C to stop)",
        EVENTS_CHANNEL,
        args.watchdog_seconds,
    )
    try:
        run(
            queue,
            orchestrator,
            stale_after_seconds=args.watchdog_seconds,
            task_port=task_port,
            task_event_emit=event_emit,
            task_evidence_client=evidence_client,
            task_stdout_reader=default_stdout_reader,
            # #931 dispatch-dedup: reuse the one evidence client for the
            # drain-time in-flight PR/branch fetch; sibling rows via task_queue.
            task_dedup=default_task_dedup(evidence_client),
            poller_port=queue,
        )
    except KeyboardInterrupt:
        logger.info("[wake_driver] KeyboardInterrupt — stopping")
    finally:
        # Release the pooled HTTP connections on EVERY exit path — normal return,
        # KeyboardInterrupt, or any exception (e.g. a SIGTERM handler raising).
        # The driver holds one HttpxGitHubClient for its whole lifetime; without
        # this the pooled TCP/TLS sockets leak on shutdown, and a supervised
        # restart loop (the M44 deployment shape) accumulates them across cycles
        # (MEDIUM, PR #1011 round 3). close() is idempotent.
        evidence_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
