"""Claude Max usage probe for Pillar 7 Sprint 2 (issue #297, S2-2).

Dispatcher (S2-3, #298) calls :func:`read_usage` before dispatching a task.
If ``reading.near_exhaustion`` is True, the dispatch is paused (the task
stays in ``task_queue`` for the next tick). Every probe failure is
*conservative false-safe* — we return a reading with ``near_exhaustion=True``
rather than raising, so a broken probe never causes a flood of dispatches.

Probe source
============

Claude Max does not expose a live-quota API to end users. This module
counts *successful* task-dispatcher audit rows since the current window
started. That's the authoritative record of "how much budget this
jurisdiction has consumed". If we later get a real API, swap
:class:`StaticBudgetProbe` for a new implementation — dispatcher only
depends on the :class:`UsageProbe` Protocol.

Configuration
=============

All knobs live in env vars with conservative defaults:

- ``CLAUDE_USAGE_WINDOW_HOURS``          — rolling window size (default 5h)
- ``CLAUDE_USAGE_BUDGET``                — dispatches allowed per window (default 100)
- ``CLAUDE_USAGE_NEAR_EXHAUSTION_PERCENT`` — headroom threshold (default 15)
- ``CLAUDE_USAGE_CACHE_TTL_SECONDS``     — probe cache TTL (default 300)

Cache
=====

:class:`CachedProbe` memoizes the reading for ``ttl_seconds`` in process
memory. Dispatcher runs in a single long-lived APScheduler process, so
cross-process cache (Supabase) adds complexity without benefit. A restart
invalidates the cache; the first tick after restart re-probes.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)


DEFAULT_WINDOW_HOURS = 5
DEFAULT_BUDGET = 100
DEFAULT_NEAR_EXHAUSTION_PERCENT = 15
DEFAULT_CACHE_TTL_SECONDS = 300

DISPATCHER_AGENT_ID = "task-dispatcher"
DISPATCH_ACTION = "dispatch"


@dataclass(frozen=True)
class UsageReading:
    """Snapshot of dispatcher's budget headroom in the current window.

    Dispatcher's gate is ``not reading.near_exhaustion``. The other fields
    are surfaced for audit and dashboards.
    """

    limit_window: timedelta
    used: int
    total: int
    reset_at: datetime
    near_exhaustion: bool

    @property
    def headroom_ratio(self) -> float:
        """Fraction of budget remaining — 0.0 means exhausted, 1.0 means empty.

        Returns 0.0 if ``total`` is zero or negative, matching the false-safe
        contract: ``total=0`` is also what :func:`_false_safe_reading` produces.
        """
        if self.total <= 0:
            return 0.0
        return max(0.0, (self.total - self.used) / self.total)


class UsageProbeError(RuntimeError):
    """Raised by concrete probes when a reading cannot be produced."""


class UsageProbe(Protocol):
    """Anything that produces a :class:`UsageReading`."""

    def read(self) -> UsageReading: ...


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "[usage_probe] %s is not an int (%r) -- using default %s", name, raw, default
        )
        return default


# ---------------------------------------------------------------------------
# Concrete probe: static budget + audit_log count
# ---------------------------------------------------------------------------


class StaticBudgetProbe:
    """Reading = (budget, count of successful dispatcher audit rows since window start).

    ``supabase`` may be a module (``agents.supabase_client``) exposing
    ``get_client()`` or a pre-built client. ``None`` means "no external source"
    — the probe reports ``used=0`` so tests can exercise headroom math
    without a live DB.
    """

    def __init__(
        self,
        *,
        supabase: Any | None = None,
        window: timedelta | None = None,
        total: int | None = None,
        near_exhaustion_percent: int | None = None,
    ) -> None:
        self._supabase = supabase
        self._window = window or timedelta(
            hours=_env_int("CLAUDE_USAGE_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)
        )
        self._total = (
            total if total is not None else _env_int("CLAUDE_USAGE_BUDGET", DEFAULT_BUDGET)
        )
        self._pct = (
            near_exhaustion_percent
            if near_exhaustion_percent is not None
            else _env_int("CLAUDE_USAGE_NEAR_EXHAUSTION_PERCENT", DEFAULT_NEAR_EXHAUSTION_PERCENT)
        )

    def read(self) -> UsageReading:
        now = _now_utc()
        window_start = now - self._window
        # reset_at is an upper bound on "budget refills in at most this long".
        # Claude Max 5-hour windows aren't aligned to a public clock pivot,
        # so "now + window" is the most honest answer we can give without an
        # API. Dispatcher only uses near_exhaustion; reset_at is informational.
        reset_at = now + self._window
        used = self._count_dispatches_since(window_start)
        used_capped = min(used, self._total) if self._total > 0 else used
        total = max(self._total, 0)
        if total == 0:
            near = True
        else:
            headroom_pct = 100 - int(used_capped * 100 / total)
            near = headroom_pct <= self._pct
        return UsageReading(
            limit_window=self._window,
            used=used_capped,
            total=total,
            reset_at=reset_at,
            near_exhaustion=near,
        )

    def _count_dispatches_since(self, window_start: datetime) -> int:
        """Count successful task-dispatcher audit rows since ``window_start``.

        Raises :class:`UsageProbeError` on backend failure so the outer
        :func:`read_usage` wrapper can convert it to a false-safe reading.
        """
        if self._supabase is None:
            return 0
        try:
            client = (
                self._supabase.get_client()
                if hasattr(self._supabase, "get_client")
                else self._supabase
            )
            resp = (
                client.table("audit_log")
                .select("id", count="exact")
                .eq("agent_id", DISPATCHER_AGENT_ID)
                .eq("action", DISPATCH_ACTION)
                .eq("outcome", "success")
                .gte("timestamp", window_start.isoformat())
                .execute()
            )
            return int(getattr(resp, "count", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — wrap any backend failure
            raise UsageProbeError(f"audit_log dispatch count failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Cache decorator
# ---------------------------------------------------------------------------


class CachedProbe:
    """Memoize ``inner.read()`` for ``ttl_seconds`` using a monotonic clock.

    ``invalidate()`` drops the cached value — dispatcher can call it after a
    confirmed dispatch that shifts headroom materially.
    """

    def __init__(
        self,
        inner: UsageProbe,
        *,
        ttl_seconds: int | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else _env_int("CLAUDE_USAGE_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)
        )
        self._now = now
        self._cached: UsageReading | None = None
        self._cached_at: float | None = None

    def read(self) -> UsageReading:
        now = self._now()
        if (
            self._cached is not None
            and self._cached_at is not None
            and now - self._cached_at < self._ttl
        ):
            return self._cached
        reading = self._inner.read()
        self._cached = reading
        self._cached_at = now
        return reading

    def invalidate(self) -> None:
        self._cached = None
        self._cached_at = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def read_usage(probe: UsageProbe | None = None) -> UsageReading:
    """Dispatcher-safe wrapper: never raises, never dispatches on failure.

    A probe exception is logged and replaced with
    :func:`_false_safe_reading` — ``near_exhaustion=True`` so the
    dispatcher pauses until the probe recovers.
    """
    if probe is None:
        from agents import supabase_client

        probe = CachedProbe(StaticBudgetProbe(supabase=supabase_client))
    try:
        return probe.read()
    except Exception as exc:  # noqa: BLE001 — false-safe contract
        logger.warning(
            "[usage_probe] probe failed -- returning false-safe reading (near_exhaustion=True): %s",
            exc,
        )
        return _false_safe_reading()


def _false_safe_reading() -> UsageReading:
    """Reading that tells the dispatcher *do not dispatch*."""
    return UsageReading(
        limit_window=timedelta(hours=_env_int("CLAUDE_USAGE_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)),
        used=0,
        total=0,
        reset_at=_now_utc(),
        near_exhaustion=True,
    )
