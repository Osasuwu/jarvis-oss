"""PID sidecar for restart-surviving liveness (#952).

**Single-driver-per-device invariant**: exactly one :class:`wake_driver.WakeDriver`
per machine supervises the sidecar directory. Multiple drivers on one device
competing for the same PIDs will corrupt liveness tracking. This is not a
distributed lock — it is a deployment assumption. Enforce via your deployment
automation (one cron entry per device, one systemd service per device, etc.).

The sidecar persists process identity ({pid, create_time, task_id, spawned_at})
to disk as JSON files in a shared directory. On driver boot, :func:`boot_scan_sidecars`
re-adopts live processes by PID + create_time matching, folding them into the
in-memory liveness map. If a process has exited or the create_time no longer matches
(e.g., PID recycle after a long delay), the orphan is tree-killed as a safety backstop.

**Atomicity model**: writes use tmp → replace to prevent partial-file corruption.
Reads are racy (file may be deleted concurrently), but that's safe — we tolerate
missing files (re-read the task_queue when the sidecar is gone). Deletes are
unsynchronized (sidecar can be deleted between a poll and a task-state write),
but the task record is the source of truth; a missing sidecar just means liveness
tracking degraded gracefully.

**Adoption tolerance**: create_time matching allows 1.0s clock jitter by default.
If the OS clock drifts >1s between spawn and boot, adoption will fail and the
process is tree-killed as an orphan. This is acceptable because:
  1. Modern systems do NTP and tolerate <1s drift.
  2. A 1s tolerance catches fork-call delays and process-table latency.
  3. Adoption failure is safe — the task row is requeued by the reaper.

**Module path anchoring**: SIDECAR_DIR is relative to this file's location
(Path(__file__).parent.parent / "logs" / "executor"), not to cwd. This enables
cross-context portability — the sidecar dir is the same whether wake_driver is
invoked from a cron, systemd, or manual CLI in a different directory.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level sidecar directory, relative to this file location.
SIDECAR_DIR = Path(__file__).parent.parent / "logs" / "executor"


@dataclass
class SidecarEntry:
    """One task's process identity, persisted on disk."""

    task_id: str
    pid: int
    create_time: float
    spawned_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "task_id": self.task_id,
            "pid": self.pid,
            "create_time": self.create_time,
            "spawned_at": self.spawned_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SidecarEntry:
        """Reconstruct from dict."""
        return cls(
            task_id=d["task_id"],
            pid=d["pid"],
            create_time=d["create_time"],
            spawned_at=datetime.fromisoformat(d["spawned_at"]),
        )


def write_sidecar(task_id: str, pid: int, create_time: float) -> None:
    """Persist process identity to disk, atomically.

    Creates SIDECAR_DIR if missing. Writes via tmp+replace to prevent partial
    corruption on crash.
    """
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    entry = SidecarEntry(task_id=task_id, pid=pid, create_time=create_time)
    sidecar_file = SIDECAR_DIR / f"{task_id}.json"
    tmp_file = sidecar_file.with_suffix(".tmp")

    try:
        with tmp_file.open("w") as f:
            json.dump(entry.to_dict(), f)
        os.replace(tmp_file, sidecar_file)
        logger.debug("wrote sidecar task_id=%s pid=%s", task_id, pid)
    except Exception as e:
        logger.exception("write_sidecar failed task_id=%s: %s", task_id, e)
        tmp_file.unlink(missing_ok=True)


def read_sidecar(task_id: str) -> SidecarEntry | None:
    """Read process identity from disk.

    Returns None if file does not exist or is malformed.
    """
    sidecar_file = SIDECAR_DIR / f"{task_id}.json"
    try:
        if sidecar_file.exists():
            with sidecar_file.open() as f:
                data = json.load(f)
            return SidecarEntry.from_dict(data)
    except Exception as e:
        logger.exception("read_sidecar failed task_id=%s: %s", task_id, e)
    return None


def delete_sidecar(task_id: str) -> None:
    """Remove process identity file."""
    sidecar_file = SIDECAR_DIR / f"{task_id}.json"
    try:
        sidecar_file.unlink(missing_ok=True)
        logger.debug("deleted sidecar task_id=%s", task_id)
    except Exception as e:
        logger.exception("delete_sidecar failed task_id=%s: %s", task_id, e)


# Sentinel exit code reported for an *adopted* process that has exited. We
# cannot read the true exit status of a process we did not spawn — a real
# ``psutil.Process`` has no ``returncode`` and no ``poll()``, only liveness via
# ``is_running()``. Per #952 AC5 ("unknown exit → failed, never done") a
# completed adopted process must close its row as ``failed``, so poll_exit
# returns this non-zero sentinel (any non-zero value routes to ``failed`` in
# task_dispatch.poll_completions). Path A re-drives the real outcome via events.
ADOPTED_PROCESS_UNKNOWN_EXIT = -1


def poll_exit(proc: Any) -> int | None:
    """Return a tracked process's exit code, or None while it is still running.

    Duck-typed across the two handle kinds the driver tracks:

    - ``subprocess.Popen`` (freshly spawned): ``proc.poll()`` gives the real exit
      code — 0 clean, non-zero failure — or None while running.
    - ``psutil.Process`` (adopted on restart): a real ``psutil.Process`` exposes
      neither ``poll()`` nor ``returncode``; ``is_running()`` is the only signal
      available, and the true exit code of a process we did not spawn is
      unknowable. A still-running adopted process returns None; an exited one
      returns :data:`ADOPTED_PROCESS_UNKNOWN_EXIT` (non-zero) so the caller marks
      the row ``failed`` rather than ``done`` (#952 AC5).

    Popen is checked first because it is the common (freshly-spawned) handle; the
    two real types are disjoint (Popen has no ``is_running``, psutil.Process has
    no ``poll``), so order only affects which branch a permissive mock takes.

    Exceptions from the underlying ``poll()`` / ``is_running()`` are **not**
    swallowed here — they propagate so the callers' per-row isolation
    (``poll_completions`` / ``kill_runaways`` drop the bad entry, and
    ``wake_driver.tick`` isolates the completion-poll half from the runaway-killer
    half — review #957-6) decides what to do with a broken handle. Converting a
    poll blowup into a false ``None`` ("still running") here would let the runaway
    killer act on a process whose state we never actually read.
    """
    if hasattr(proc, "poll"):
        # subprocess.Popen — real exit code (or None while running).
        return proc.poll()
    if hasattr(proc, "is_running"):
        # psutil.Process (adopted) — no real exit code; running → None,
        # exited → non-zero sentinel so the row closes as ``failed``.
        return None if proc.is_running() else ADOPTED_PROCESS_UNKNOWN_EXIT
    # Unrecognized handle kind — treat as still running; the runaway and
    # orphan reapers are the backstop rather than a false ``done``.
    logger.warning("poll_exit: unrecognized process handle type %r", type(proc))
    return None


def adopt_task(
    task_id: str, pid: int, create_time: float, *, create_time_tolerance_sec: float = 1.0
) -> Any | None:
    """Re-adopt a process on boot.

    Checks if ``pid`` is alive AND its OS-reported ``create_time`` matches the
    sidecar's recorded ``create_time`` within ``create_time_tolerance_sec``.
    Both checks are required: a live PID alone is not enough, because PIDs are
    recycled — without the create_time match we could adopt an unrelated process
    that happened to inherit the recycled PID (#952 AC3). ``create_time`` is a
    required argument for exactly this reason; a default would silently disable
    PID-recycle protection if a caller forgot to pass it.

    Returns a psutil.Process if adoption succeeds, None otherwise.

    Requires psutil. If it's not available, logs a warning and returns None.
    """
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available; skipping adoption task_id=%s", task_id)
        return None

    try:
        proc = psutil.Process(pid)
        # Verify process is alive and create_time matches.
        if not proc.is_running():
            logger.debug(
                "adopt_task failed: process exited task_id=%s pid=%s", task_id, pid
            )
            return None

        actual_create_time = proc.create_time()
        if abs(actual_create_time - create_time) > create_time_tolerance_sec:
            logger.debug(
                "adopt_task failed: create_time mismatch task_id=%s pid=%s "
                "expected=%s actual=%s",
                task_id,
                pid,
                create_time,
                actual_create_time,
            )
            return None

        logger.info("adopted process task_id=%s pid=%s", task_id, pid)
        return proc
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        logger.debug("adopt_task failed: %s task_id=%s pid=%s", type(e).__name__, task_id, pid)
        return None
    except Exception as e:
        logger.exception("adopt_task exception task_id=%s pid=%s: %s", task_id, pid, e)
        return None


def kill_orphan_process(task_id: str, pid: int, reason: str = "") -> None:
    """Tree-kill an orphan process on terminal row transition.

    Logs a warning, deletes the sidecar, and kills the process tree.
    """
    try:
        import psutil
    except ImportError:
        logger.warning(
            "psutil not available; cannot kill orphan task_id=%s pid=%s", task_id, pid
        )
        return

    try:
        proc = psutil.Process(pid)
        if proc.is_running():
            logger.warning(
                "killing orphan process task_id=%s pid=%s %s",
                task_id,
                pid,
                reason,
            )
            proc.kill()
            # Give it a moment to die.
            try:
                proc.wait(timeout=1.0)
            except psutil.TimeoutExpired:
                logger.warning("orphan did not die after SIGKILL task_id=%s pid=%s", task_id, pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        logger.debug(
            "kill_orphan failed: %s task_id=%s pid=%s", type(e).__name__, task_id, pid
        )
    except Exception as e:
        logger.exception("kill_orphan exception task_id=%s pid=%s: %s", task_id, pid, e)
    finally:
        delete_sidecar(task_id)


def boot_scan_sidecars() -> list[tuple[str, int, float]]:
    """Scan all sidecars at boot, return [(task_id, pid, create_time), ...].

    Only returns entries for sidecars that exist. Malformed sidecars are skipped
    with a warning but do not stop the scan.
    """
    if not SIDECAR_DIR.exists():
        return []

    adopted = []
    try:
        for sidecar_file in SIDECAR_DIR.glob("*.json"):
            task_id = sidecar_file.stem
            entry = read_sidecar(task_id)
            if entry:
                adopted.append((entry.task_id, entry.pid, entry.create_time))
            else:
                logger.warning("malformed sidecar skipped: %s", sidecar_file)
    except Exception as e:
        logger.exception("boot_scan_sidecars failed: %s", e)

    return adopted


class Sidecar:
    """Encapsulates PID sidecar lifecycle for a wake_driver instance."""

    def __init__(self) -> None:
        """Initialize sidecar manager."""
        pass

    def record_spawn(self, task_id: str, pid: int, create_time: float) -> None:
        """Record a newly-spawned process."""
        write_sidecar(task_id, pid, create_time)

    def poll_exit(self, proc: Any) -> int | None:
        """Poll a process for exit; returns exit code or None."""
        return poll_exit(proc)

    def delete_sidecar_file(self, task_id: str) -> None:
        """Delete the sidecar file for a task."""
        delete_sidecar(task_id)

    def adopt_live_processes(self) -> list[tuple[str, Any]]:
        """At boot, re-adopt all live processes.

        Returns [(task_id, psutil.Process), ...] for processes successfully adopted.
        """
        adopted_procs = []
        for task_id, pid, create_time in boot_scan_sidecars():
            # Pass the sidecar's recorded create_time so adopt_task can verify it
            # against the OS-reported value — guards against PID recycle (#952 AC3).
            proc = adopt_task(task_id, pid, create_time)
            if proc:
                adopted_procs.append((task_id, proc))
            else:
                # Process dead or mismatch; kill as orphan safety backstop.
                kill_orphan_process(task_id, pid, reason="boot adoption failed")
        return adopted_procs

    def kill_orphan(self, task_id: str, pid: int) -> None:
        """Kill an orphan process on terminal task row transition."""
        kill_orphan_process(task_id, pid, reason="terminal transition")
