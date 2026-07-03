"""wake_driver — crash-safe LISTEN/NOTIFY cold-boot loop (#743).

The wake_driver replaces the retired APScheduler resident scheduler. It is a
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
- The orchestrator is **injected** (a ``Callable[[dict], None]``). The default
  is a trivial no-op stub (this slice tests wake mechanics without the real
  model). wake_driver deliberately does **not** import
  :func:`agents.orchestrator.handle_event` — the driver is a decisionless
  program and the router is a separate concern.
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
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from dotenv import load_dotenv

from agents.config import load_config
from agents.pid_sidecar import Sidecar
from agents.task_dispatch import (
    DEFAULT_CLAIMED_STALE_SECONDS,
    DEFAULT_RUNNING_REAP_SECONDS,
    EventEmit,
    ReadUsage,
    ResolveBinary,
    Spawn,
    SupabaseTaskQueue,
    TaskQueuePort,
    TrackedProc,
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

# The NOTIFY channel from the #739 substrate (notify_events_insert).
EVENTS_CHANNEL = "events"

# The NOTIFY channel from the #922 task_queue substrate (notify_task_queue_insert).
# Fires when a task row reaches ``pending`` after a cap-freed transition or fresh
# insert, waking the driver to drain without waiting for the idle timeout.
TASK_QUEUE_CHANNEL = "task_queue"

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

    def reclaim_stale(self, *, older_than_seconds: float) -> int:
        """Return ``claimed`` rows older than the threshold to ``pending``."""

    def wait_for_wake(self, *, timeout_seconds: float | None) -> bool:
        """Block until a NOTIFY arrives or the timeout elapses.

        Returns ``True`` on a wake signal, ``False`` on timeout.
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


def drain_pending(port: EventQueuePort, orchestrator: Orchestrator) -> int:
    """Drain every ``pending`` event, one at a time, until the queue is empty.

    Claims, hands the event to ``orchestrator``, then commits ``processed`` —
    advancing to the next event as soon as the prior finishes (no interval).

    Crash-safety: if ``orchestrator`` raises, ``mark_processed`` is **not**
    reached, so the event is left ``claimed`` (recoverable by the watchdog)
    rather than lost or marked done. The exception propagates — a mid-tick
    failure aborts the drain, exactly as a process kill would.
    """
    processed = 0
    while (event := port.claim_next()) is not None:
        orchestrator(event)
        port.mark_processed(str(event["id"]))
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
) -> TickResult:
    """One unit of work — ordered steps (#909 AC1, #921 AC3, #745 Path B)::

        poll_completions() + kill_runaways()                  # Step 0, #921
        → reclaim_stale(events)                               # Step 1, event watchdog
        → reclaim_stale_tasks()                               # Step 2, task watchdog
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

    The task steps (0, 2 and 4) are each isolated in their own try/except: the
    task_queue rides supabase-py while events ride psycopg, so a task-store
    outage is an independent failure mode. It must not block the event drain
    (Step 3) — events are the primary wake path. A failing task step is logged
    and its rows stay in place (``claimed``/``running`` → swept next tick;
    ``pending`` → re-drained next tick), exactly as a crash would leave them.
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
    processed = drain_pending(port, orchestrator)

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
    ``task_queue`` (#909) and the loop owns the **liveness map** (#921): one
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
    """
    keep_going = should_continue or (lambda: True)
    procs = task_procs if task_procs is not None else ({} if task_port is not None else None)

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

    def __init__(self, conn: psycopg.Connection, *, claimer: str = CLAIMER) -> None:
        self._conn = conn
        self._claimer = claimer
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


def _build_psycopg_queue() -> PsycopgEventQueue:
    import psycopg

    cfg = load_config()
    conn = psycopg.connect(cfg.postgres_url, autocommit=False)
    return PsycopgEventQueue(conn)


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
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
    args = parser.parse_args()

    queue = _build_psycopg_queue()
    task_port = SupabaseTaskQueue()  # tasks ride supabase-py; events ride psycopg

    # #953 — evidence + terminal-event emission wiring. The repo scopes both the
    # GitHub evidence client (PR lookups) and the emitted events; the stdout
    # reader recovers a claimed PR number from the executor's JSON log when the
    # fresh-shape branch lookup comes up empty (AC3). The orchestrator stays the
    # out-of-scope stub — #953 is detection + emission, not live routing.
    from agents.github_client import default_github_client

    repo = os.environ.get("GITHUB_REPO", "your-username/your-repo")
    evidence_client = default_github_client()
    # Build the Supabase client ONCE and inject it into the emitter (MAJOR, PR
    # #1011). With client=None, store_event calls get_client() per event — a fresh
    # create_client() (and its connection setup) on every terminal event. A
    # long-running driver emits thousands; one shared client amortizes that.
    from agents.supabase_client import get_client

    event_client = get_client()
    event_emit = _default_event_emit(repo=repo, client=event_client)

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
        result = tick(
            queue,
            default_orchestrator,
            stale_after_seconds=args.watchdog_seconds,
            task_port=task_port,
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
        return 0

    logger.info(
        "[wake_driver] listening on '%s' channel (watchdog=%ss, Ctrl-C to stop)",
        EVENTS_CHANNEL,
        args.watchdog_seconds,
    )
    try:
        run(
            queue,
            default_orchestrator,
            stale_after_seconds=args.watchdog_seconds,
            task_port=task_port,
            task_event_emit=event_emit,
            task_evidence_client=evidence_client,
            task_stdout_reader=default_stdout_reader,
            # #931 dispatch-dedup: reuse the one evidence client for the
            # drain-time in-flight PR/branch fetch; sibling rows via task_queue.
            task_dedup=default_task_dedup(evidence_client),
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
