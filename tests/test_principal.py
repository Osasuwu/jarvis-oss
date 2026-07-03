"""Tests for scripts/principal.py — principal detection (#426 + #429)."""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import principal  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip JARVIS_PRINCIPAL and headless markers before each test so the
    detection chain starts from a clean slate.
    """
    monkeypatch.delenv("JARVIS_PRINCIPAL", raising=False)
    for name in principal._HEADLESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ── Explicit env var (primary signal) ────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["live", "autonomous", "subagent", "supervised"],
)
def test_explicit_env_each_valid_value(monkeypatch, value):
    """Explicit JARVIS_PRINCIPAL wins over default."""
    monkeypatch.setenv("JARVIS_PRINCIPAL", value)
    assert principal.detect() == value


def test_explicit_env_uppercase_normalized(monkeypatch):
    monkeypatch.setenv("JARVIS_PRINCIPAL", "LIVE")
    assert principal.detect() == "live"


def test_explicit_env_whitespace_stripped(monkeypatch):
    monkeypatch.setenv("JARVIS_PRINCIPAL", "  autonomous  ")
    assert principal.detect() == "autonomous"


def test_explicit_env_invalid_falls_through_to_default_live(monkeypatch):
    """Invalid value is ignored; default chain returns live (#429).

    Earlier behavior fell to autonomous via isatty fallback. After the #429
    fix, default is live. Autonomous launchers must set the env explicitly.
    """
    monkeypatch.setenv("JARVIS_PRINCIPAL", "garbage")
    assert principal.detect() == "live"


def test_explicit_env_empty_treated_as_unset(monkeypatch):
    monkeypatch.setenv("JARVIS_PRINCIPAL", "")
    assert principal.detect() == "live"


# ── Headless env (suspenders) ────────────────────────────────────────


@pytest.mark.parametrize("var_name", principal._HEADLESS_ENV_VARS)
def test_headless_env_forces_autonomous(monkeypatch, var_name):
    """Headless env vars route to autonomous regardless of other signals."""
    monkeypatch.setenv(var_name, "1")
    assert principal.detect() == "autonomous"


@pytest.mark.parametrize("falsy", ["0", "false", "no", "FALSE", " "])
def test_headless_env_falsy_values_ignored(monkeypatch, falsy):
    """A headless env set to a falsy value isn't a real signal."""
    monkeypatch.setenv("CLAUDE_CODE_NON_INTERACTIVE", falsy)
    assert principal.detect() == "live"


def test_explicit_env_overrides_headless(monkeypatch):
    """Explicit JARVIS_PRINCIPAL still wins over headless env."""
    monkeypatch.setenv("CLAUDE_CODE_NON_INTERACTIVE", "1")
    monkeypatch.setenv("JARVIS_PRINCIPAL", "subagent")
    assert principal.detect() == "subagent"


# ── Default behavior ─────────────────────────────────────────────────


def test_default_with_no_signals_returns_live():
    """No env, no headless markers → live (#429).

    This is the contract: interactive sessions don't need explicit setup.
    Autonomous launchers must set JARVIS_PRINCIPAL=autonomous explicitly.
    """
    assert principal.detect() == "live"


def test_piped_stdin_does_not_force_autonomous(monkeypatch):
    """Hook subprocesses always have piped stdin (the JSON tool_input).

    Regression guard for #429: previously the isatty fallback misclassified
    every hook invocation as autonomous because hook stdin is never a TTY.
    With the fallback removed, piped stdin doesn't change the verdict.
    """
    # Simulate hook subprocess: stdin replaced with a non-TTY pipe-like.
    monkeypatch.setattr(sys, "stdin", io.StringIO("{\"tool_input\": {}}"))
    assert principal.detect() == "live"


# ── Unit primitives ──────────────────────────────────────────────────


def test_explicit_env_helper_returns_none_on_invalid(monkeypatch):
    monkeypatch.setenv("JARVIS_PRINCIPAL", "garbage")
    assert principal._explicit_env() is None


def test_explicit_env_helper_returns_value_on_valid(monkeypatch):
    monkeypatch.setenv("JARVIS_PRINCIPAL", "supervised")
    assert principal._explicit_env() == "supervised"


def test_is_headless_env_with_no_vars():
    assert principal._is_headless_env() is False


@pytest.mark.parametrize("var_name", principal._HEADLESS_ENV_VARS)
def test_is_headless_env_with_each_var(monkeypatch, var_name):
    monkeypatch.setenv(var_name, "1")
    assert principal._is_headless_env() is True
