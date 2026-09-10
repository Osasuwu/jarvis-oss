"""Shared pytest fixtures + stub modules for the test suite.

Stubs `supabase`/`httpx` (if not installed) and `dotenv` at collection time
so tests that import modules depending on them don't need the full set
installed — historically each test file duplicated this setup, which
drifted and broke when new helpers needed testing (see #254 rework).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import types

import pytest
from unittest.mock import MagicMock


# ---- Conditional stubs (don't shadow real installs other tests need) ----

try:
    import supabase  # noqa: F401
except ImportError:
    _supabase_mod = types.ModuleType("supabase")
    _supabase_mod.create_client = MagicMock
    sys.modules["supabase"] = _supabase_mod

try:
    import httpx  # noqa: F401
except ImportError:
    sys.modules["httpx"] = types.ModuleType("httpx")

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", _dotenv)

# ---- Path + env setup ----

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "scripts"))
sys.path.insert(0, str(_repo_root))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")


# ---------------------------------------------------------------------------
# Persistent-environment pollution guard (#1192)
# ---------------------------------------------------------------------------
# Incident 2026-07-15: installer tests ran a real `setx JARVIS_HOME
# <pytest tmp_path>`, leaving the developer's User-scope JARVIS_HOME pointing
# at a deleted temp dir. Tests must stub installer._set_env (or pass
# --skip-env); this guard catches any mechanism that slips through, from any
# test file in the suite.


def _persistent_env_snapshot() -> dict[str, object]:
    """User-scope JARVIS_HOME (Windows registry) / shell rc bytes (POSIX)."""
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, "JARVIS_HOME")[0]
        except OSError:
            value = None
        return {"HKCU:Environment:JARVIS_HOME": value}
    return {
        str(rc): (rc.read_bytes() if rc.exists() else None)
        for rc in (Path.home() / ".bashrc", Path.home() / ".zshrc")
    }


@pytest.fixture(scope="session", autouse=True)
def _no_persistent_env_pollution():
    """Fail the run if any test mutated the machine's persistent environment."""
    before = _persistent_env_snapshot()
    yield
    after = _persistent_env_snapshot()
    assert after == before, (
        f"test run mutated persistent environment: {before!r} -> {after!r}; "
        "a test reached real setx / shell rc files — stub installer._set_env "
        "or pass --skip-env (#1192)"
    )


# ---------------------------------------------------------------------------
# Live-Supabase write guard (#1121 step 16 fallout)
# ---------------------------------------------------------------------------
# Incident 2026-09-02: `pytest tests/reactive_core` wrote 392 real
# `drain_infra_preflight_failure` rows into the production events table in six
# minutes, and wake_driver began fanning them into owner escalations. Mechanism:
# 44 of 46 `drain_tasks(...)` call sites pass neither `check_infra_available`
# nor `infra_event_emitter`, so both production defaults ran; on a pytest
# process that cannot see docker/node on PATH the pre-flight raises and the real
# `default_emit_infra_preflight_event` calls `get_client()`, which `load_config`
# happily fills from the developer's `.env`.
#
# The env vars set above are decoys — `setdefault` loses to a real .env, so
# "tests use a test URL" was never true. This guard is the actual boundary: any
# test that reaches a live client fails, loudly, naming itself. A test that
# legitimately needs a client stubs `get_client` itself, which overrides this.


@pytest.fixture(autouse=True)
def _no_live_supabase(monkeypatch):
    """Fail any test that constructs a real Supabase client."""

    def _blocked(*_args, **_kwargs):
        raise AssertionError(
            "test constructed a LIVE Supabase client - it would read/write the "
            "production database. Pass a stub client (or monkeypatch get_client) "
            "in the test instead."
        )

    monkeypatch.setattr("supabase.create_client", _blocked, raising=False)
