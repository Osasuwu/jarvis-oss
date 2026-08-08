"""Unit tests for the Claude Max usage probe (issue #297, S2-2).

Uses a stub Supabase client + a fake clock so these tests run in-process
without a live DB or real time passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from agents import usage_probe
from agents.usage_probe import (
    CachedProbe,
    StaticBudgetProbe,
    UsageProbe,
    UsageProbeError,
    UsageReading,
    _false_safe_reading,
    read_usage,
)


# ---------------------------------------------------------------------------
# Helpers: stub supabase query chain + fake probe.
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    count: int


class _FakeQuery:
    """Records the method chain so tests can assert filter shape."""

    def __init__(self, count: int, recorder: dict[str, Any]) -> None:
        self._count = count
        self._recorder = recorder

    def select(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        self._recorder["select_kwargs"] = kwargs
        return self

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._recorder.setdefault("eq", []).append((col, val))
        return self

    def gte(self, col: str, val: Any) -> "_FakeQuery":
        self._recorder.setdefault("gte", []).append((col, val))
        return self

    def execute(self) -> _FakeResponse:
        return _FakeResponse(count=self._count)


class _FakeClient:
    def __init__(self, count: int) -> None:
        self.count = count
        self.recorder: dict[str, Any] = {}

    def table(self, name: str) -> _FakeQuery:
        self.recorder["table"] = name
        return _FakeQuery(self.count, self.recorder)


class _ExplodingClient:
    def table(self, name: str) -> Any:
        raise RuntimeError("simulated backend failure")


class _StubProbe:
    """Deterministic probe for testing the cache and wrapper."""

    def __init__(self, reading: UsageReading) -> None:
        self.reading = reading
        self.calls = 0

    def read(self) -> UsageReading:
        self.calls += 1
        return self.reading


class _FlakyProbe:
    def read(self) -> UsageReading:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# UsageReading.headroom_ratio
# ---------------------------------------------------------------------------


def test_headroom_ratio_full() -> None:
    r = UsageReading(
        limit_window=timedelta(hours=5),
        used=0,
        total=100,
        reset_at=usage_probe._now_utc(),
        near_exhaustion=False,
    )
    assert r.headroom_ratio == 1.0


def test_headroom_ratio_half() -> None:
    r = UsageReading(
        limit_window=timedelta(hours=5),
        used=50,
        total=100,
        reset_at=usage_probe._now_utc(),
        near_exhaustion=False,
    )
    assert r.headroom_ratio == 0.5


def test_headroom_ratio_zero_total_returns_zero() -> None:
    """False-safe reading uses total=0 — headroom must be 0, not a ZeroDivisionError."""
    r = _false_safe_reading()
    assert r.headroom_ratio == 0.0


def test_headroom_ratio_clamped_to_zero_when_overused() -> None:
    r = UsageReading(
        limit_window=timedelta(hours=5),
        used=150,
        total=100,
        reset_at=usage_probe._now_utc(),
        near_exhaustion=True,
    )
    assert r.headroom_ratio == 0.0


# ---------------------------------------------------------------------------
# StaticBudgetProbe — happy paths
# ---------------------------------------------------------------------------


def test_probe_without_supabase_reports_zero_used() -> None:
    probe = StaticBudgetProbe(supabase=None, total=100, near_exhaustion_percent=15)
    reading = probe.read()
    assert reading.used == 0
    assert reading.total == 100
    assert reading.near_exhaustion is False
    assert reading.limit_window == timedelta(hours=5)


def test_probe_counts_dispatcher_audit_rows() -> None:
    client = _FakeClient(count=42)
    probe = StaticBudgetProbe(supabase=client, total=100, near_exhaustion_percent=15)
    reading = probe.read()
    assert reading.used == 42
    assert reading.total == 100
    # Near-exhaustion triggers at <=15% headroom → used=42 leaves 58% headroom.
    assert reading.near_exhaustion is False
    # The filter chain must have scoped to task-dispatcher / dispatch / success.
    eqs = dict(client.recorder["eq"])
    assert eqs["agent_id"] == "task-dispatcher"
    assert eqs["action"] == "dispatch"
    assert eqs["outcome"] == "success"
    # And filtered by window_start in timestamp.
    # (audit_log's timestamp column is the canonical name — see
    # mcp-memory/schema.sql and docs/agents/e2e-test.md:137. The prior
    # "created_at" assertion matched the code but not the real schema,
    # so unit tests were green while prod probes 400'd and the
    # dispatcher escalated every tick on false-safe near_exhaustion.)
    gtes = dict(client.recorder["gte"])
    assert "timestamp" in gtes


def test_probe_near_exhaustion_flips_at_threshold() -> None:
    """85 used / 100 total = 15% headroom → near_exhaustion at <=15%."""
    client = _FakeClient(count=85)
    probe = StaticBudgetProbe(supabase=client, total=100, near_exhaustion_percent=15)
    reading = probe.read()
    assert reading.used == 85
    assert reading.near_exhaustion is True


def test_probe_not_near_exhaustion_just_below_threshold() -> None:
    """84 used / 100 total = 16% headroom → NOT near_exhaustion at <=15%."""
    client = _FakeClient(count=84)
    probe = StaticBudgetProbe(supabase=client, total=100, near_exhaustion_percent=15)
    reading = probe.read()
    assert reading.near_exhaustion is False


def test_probe_used_capped_at_total() -> None:
    """Over-use in the window (shouldn't happen, but be resilient) caps at total."""
    client = _FakeClient(count=200)
    probe = StaticBudgetProbe(supabase=client, total=100, near_exhaustion_percent=15)
    reading = probe.read()
    assert reading.used == 100
    assert reading.near_exhaustion is True


def test_probe_threshold_is_configurable() -> None:
    """Setting threshold to 50% should flip near_exhaustion at 50 used / 100."""
    client = _FakeClient(count=50)
    probe = StaticBudgetProbe(supabase=client, total=100, near_exhaustion_percent=50)
    reading = probe.read()
    assert reading.near_exhaustion is True


def test_probe_accepts_module_with_get_client() -> None:
    """supabase-module shape (agents.supabase_client) vs already-built client."""
    client = _FakeClient(count=7)

    class _Module:
        @staticmethod
        def get_client() -> _FakeClient:
            return client

    probe = StaticBudgetProbe(supabase=_Module(), total=100)
    reading = probe.read()
    assert reading.used == 7


def test_probe_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_USAGE_WINDOW_HOURS", "2")
    monkeypatch.setenv("CLAUDE_USAGE_BUDGET", "50")
    monkeypatch.setenv("CLAUDE_USAGE_NEAR_EXHAUSTION_PERCENT", "25")
    probe = StaticBudgetProbe(supabase=None)
    reading = probe.read()
    assert reading.limit_window == timedelta(hours=2)
    assert reading.total == 50


def test_probe_env_var_bad_int_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Typo in env var → warn + use default, not crash."""
    monkeypatch.setenv("CLAUDE_USAGE_BUDGET", "not-an-int")
    with caplog.at_level("WARNING"):
        probe = StaticBudgetProbe(supabase=None)
    reading = probe.read()
    assert reading.total == 100  # DEFAULT_BUDGET
    assert any("CLAUDE_USAGE_BUDGET" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# StaticBudgetProbe — failure path
# ---------------------------------------------------------------------------


def test_probe_wraps_backend_errors_in_usage_probe_error() -> None:
    probe = StaticBudgetProbe(supabase=_ExplodingClient(), total=100)
    with pytest.raises(UsageProbeError, match="audit_log dispatch count failed"):
        probe.read()


# ---------------------------------------------------------------------------
# CachedProbe — TTL behaviour
# ---------------------------------------------------------------------------


def _reading(used: int = 10, total: int = 100, near: bool = False) -> UsageReading:
    return UsageReading(
        limit_window=timedelta(hours=5),
        used=used,
        total=total,
        reset_at=usage_probe._now_utc(),
        near_exhaustion=near,
    )


def test_cached_probe_returns_cached_within_ttl() -> None:
    clock = [1000.0]
    inner = _StubProbe(_reading(used=10))
    cached = CachedProbe(inner, ttl_seconds=300, now=lambda: clock[0])

    first = cached.read()
    clock[0] += 299  # just inside TTL
    second = cached.read()

    assert first is second  # same object, from cache
    assert inner.calls == 1


def test_cached_probe_re_reads_after_ttl_expires() -> None:
    clock = [1000.0]
    inner = _StubProbe(_reading(used=10))
    cached = CachedProbe(inner, ttl_seconds=300, now=lambda: clock[0])

    cached.read()
    clock[0] += 301  # just past TTL
    cached.read()

    assert inner.calls == 2


def test_cached_probe_invalidate_forces_next_read() -> None:
    clock = [1000.0]
    inner = _StubProbe(_reading(used=10))
    cached = CachedProbe(inner, ttl_seconds=300, now=lambda: clock[0])

    cached.read()
    cached.invalidate()
    cached.read()

    assert inner.calls == 2


def test_cached_probe_ttl_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_USAGE_CACHE_TTL_SECONDS", "7")
    clock = [1000.0]
    inner = _StubProbe(_reading(used=10))
    cached = CachedProbe(inner, now=lambda: clock[0])

    cached.read()
    clock[0] += 8  # past the 7s env-specified TTL
    cached.read()

    assert inner.calls == 2


# ---------------------------------------------------------------------------
# read_usage — false-safe contract
# ---------------------------------------------------------------------------


def test_read_usage_returns_probe_reading_on_success() -> None:
    inner_reading = _reading(used=10, total=100, near=False)
    probe = _StubProbe(inner_reading)
    reading = read_usage(probe)
    assert reading is inner_reading


def test_read_usage_returns_false_safe_on_probe_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A flaky probe must NOT propagate — dispatcher must never crash on probe error."""
    with caplog.at_level("WARNING"):
        reading = read_usage(_FlakyProbe())
    assert reading.near_exhaustion is True
    assert reading.total == 0
    assert reading.headroom_ratio == 0.0
    # User-facing warning must name the module and the failure.
    assert any("probe failed" in rec.message for rec in caplog.records)


def test_false_safe_reading_says_do_not_dispatch() -> None:
    r = _false_safe_reading()
    assert r.near_exhaustion is True
    assert r.used == 0
    assert r.total == 0


# ---------------------------------------------------------------------------
# UsageProbe protocol surface — _StubProbe must satisfy it structurally.
# ---------------------------------------------------------------------------


def test_stub_probe_satisfies_protocol() -> None:
    """Sanity: any class with a .read() returning UsageReading is a UsageProbe."""
    stub: UsageProbe = _StubProbe(_reading())
    assert isinstance(stub.read(), UsageReading)
