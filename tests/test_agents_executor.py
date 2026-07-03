"""Unit tests for the executor module (salvaged from dispatcher #741).

Tests the fire-and-forget spawn primitive, env sanitization,
binary resolution, and utility hashing — no live Postgres, no
real ``claude`` binary.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _CapturedPopen:
    """Records the argv + env passed to each ``Popen`` instantiation."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {}), **kwargs})

        class _Handle:
            pid = 99999

            def poll(self) -> None:
                return None

        return _Handle()


# ---------------------------------------------------------------------------
# Module-level — surface & constants
# ---------------------------------------------------------------------------


def test_sensitive_env_keys_cover_known_variants() -> None:
    """Env-sanitization must strip every historical Anthropic env name."""
    from agents.executor import _SENSITIVE_ENV_KEYS

    assert "ANTHROPIC_API_KEY" in _SENSITIVE_ENV_KEYS
    assert "ANTHROPIC_AUTH_TOKEN" in _SENSITIVE_ENV_KEYS
    assert "CLAUDE_API_KEY" in _SENSITIVE_ENV_KEYS
    # A base-url redirect is as much a billing trap as a key: it can point the
    # spawned `claude -p` at a metered API gateway instead of the Max session.
    assert "ANTHROPIC_BASE_URL" in _SENSITIVE_ENV_KEYS
    assert "CLAUDE_BASE_URL" in _SENSITIVE_ENV_KEYS


def test_spawn_allowlist_excludes_dangerous_permissions() -> None:
    """Verify spawn whitelist has been tightened per #378."""
    from agents.executor import _SPAWN_ALLOWED_TOOLS

    assert "Bash(python:*)" not in _SPAWN_ALLOWED_TOOLS
    assert "Bash(gh:*)" not in _SPAWN_ALLOWED_TOOLS

    safe_gh_verbs = {
        "Bash(gh pr view:*)",
        "Bash(gh pr create:*)",
        "Bash(gh pr list:*)",
        "Bash(gh issue view:*)",
        "Bash(gh issue create:*)",
        "Bash(gh issue list:*)",
        "Bash(gh issue comment:*)",
        "Bash(gh api repos/*/issues:*)",
        "Bash(gh api repos/*/pulls:*)",
    }
    for verb in safe_gh_verbs:
        assert verb in _SPAWN_ALLOWED_TOOLS, f"Missing safe gh verb '{verb}'"

    for pattern in ["merge", "delete"]:
        for tool in _SPAWN_ALLOWED_TOOLS:
            assert pattern not in tool, (
                f"Destructive pattern '{pattern}' found in allowlist entry '{tool}'"
            )


# ---------------------------------------------------------------------------
# _resolve_claude_binary
# ---------------------------------------------------------------------------


def test_resolve_claude_binary_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from agents.executor import _resolve_claude_binary

    real = tmp_path / "real-claude.exe"
    real.write_text("")
    fake_env = tmp_path / "env-claude.exe"
    fake_env.write_text("")

    monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake_env))
    assert _resolve_claude_binary(override=str(real)) == str(real)


def test_resolve_claude_binary_override_must_exist(tmp_path: Any) -> None:
    from agents.executor import _resolve_claude_binary

    missing = tmp_path / "does-not-exist.exe"
    with pytest.raises(FileNotFoundError, match="override does not exist"):
        _resolve_claude_binary(override=str(missing))


def test_resolve_claude_binary_env_var_wins_over_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from agents.executor import _resolve_claude_binary

    fake = tmp_path / "claude.exe"
    fake.write_text("")

    monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake))

    assert _resolve_claude_binary() == str(fake)


def test_resolve_claude_binary_env_var_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.executor import _resolve_claude_binary

    monkeypatch.setenv("JARVIS_CLAUDE_BIN", "/no/such/file")
    with pytest.raises(FileNotFoundError, match="JARVIS_CLAUDE_BIN"):
        _resolve_claude_binary()


def test_resolve_claude_binary_falls_through_to_shutil_which(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    import shutil as _shutil
    from agents.executor import _resolve_claude_binary

    fake = tmp_path / "from-which.exe"
    fake.write_text("")

    monkeypatch.delenv("JARVIS_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(_shutil, "which", lambda name: str(fake) if name == "claude" else None)

    assert _resolve_claude_binary() == str(fake)


def test_resolve_claude_binary_raises_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil as _shutil

    import agents.executor as _executor
    from agents.executor import _resolve_claude_binary

    monkeypatch.delenv("JARVIS_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    # On Windows the 4th resolution step probes documented install paths
    # (#385). On a dev box where `claude` lives at one of those defaults,
    # the function would return that path instead of raising — a local-only
    # false failure. Neutralize the fallback so all four steps fail
    # regardless of host OS / installed binaries.
    monkeypatch.setattr(_executor, "_CLAUDE_DEFAULT_WINDOWS_PATHS", ())

    with pytest.raises(FileNotFoundError, match="JARVIS_CLAUDE_BIN"):
        _resolve_claude_binary()


# ---------------------------------------------------------------------------
# _sanitize_env
# ---------------------------------------------------------------------------


def test_sanitize_env_strips_api_key() -> None:
    from agents.executor import _sanitize_env

    src = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-leak", "HOME": "/root"}
    out = _sanitize_env(src)
    assert "ANTHROPIC_API_KEY" not in out
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/root"


def test_sanitize_env_strips_all_known_variants() -> None:
    from agents.executor import _sanitize_env

    src = {
        "SAFE": "keep",
        "ANTHROPIC_API_KEY": "a",
        "ANTHROPIC_AUTH_TOKEN": "b",
        "CLAUDE_API_KEY": "c",
        "ANTHROPIC_BASE_URL": "https://metered.example/v1",
        "CLAUDE_BASE_URL": "https://metered.example/v1",
    }
    out = _sanitize_env(src)
    assert out == {"SAFE": "keep"}


def test_sanitize_env_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.executor import _sanitize_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-stripped")
    monkeypatch.setenv("PATH_FROM_TEST", "keep")
    out = _sanitize_env()
    assert "ANTHROPIC_API_KEY" not in out
    assert out.get("PATH_FROM_TEST") == "keep"


# ---------------------------------------------------------------------------
# _hash_scope_files
# ---------------------------------------------------------------------------


def test_hash_scope_files_is_order_independent() -> None:
    from agents.executor import _hash_scope_files

    assert _hash_scope_files(["b.py", "a.py"]) == _hash_scope_files(["a.py", "b.py"])


def test_hash_scope_files_detects_added_file() -> None:
    from agents.executor import _hash_scope_files

    assert _hash_scope_files(["a.py"]) != _hash_scope_files(["a.py", "b.py"])


def test_hash_scope_files_empty_list_is_stable() -> None:
    from agents.executor import _hash_scope_files

    first = _hash_scope_files([])
    second = _hash_scope_files([])
    assert first == second
    assert len(first) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# spawn — billing-trap test
# ---------------------------------------------------------------------------


def test_spawn_passes_sanitized_env_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Billing-trap: API keys in parent env must NOT reach child env."""
    from agents.executor import spawn

    fake_claude = tmp_path / "claude.exe"
    fake_claude.write_text("")
    monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake_claude))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak-sentinel-xyz")
    monkeypatch.setenv("CLAUDE_API_KEY", "leak-sentinel-claude")
    monkeypatch.setenv("PATH_FROM_PARENT", "keep-me")

    captured = _CapturedPopen()
    result = spawn(
        "test task",
        stderr_log_dir=str(tmp_path / "logs"),
        popen=captured,
        probe=_FixedProbe(_healthy_reading()),
    )

    assert result.proc is not None, "spawn should not be throttled"
    assert not result.throttled
    assert len(captured.calls) == 1
    env = captured.calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env, "billing-trap leak: API key reached child env"
    assert "CLAUDE_API_KEY" not in env, "defensive-variant leak: CLAUDE_API_KEY reached child"
    assert env.get("PATH_FROM_PARENT") == "keep-me", "non-sensitive env must survive"


def test_spawn_uses_resolved_binary_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Spawn receives an absolute path, not the bare string ``claude``."""
    from agents.executor import spawn

    fake = tmp_path / "resolved-claude.exe"
    fake.write_text("")

    monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake))

    captured = _CapturedPopen()
    result = spawn(
        "test",
        stderr_log_dir=str(tmp_path / "logs"),
        popen=captured,
        probe=_FixedProbe(_healthy_reading()),
    )

    assert result.proc is not None, "spawn should not be throttled"
    assert len(captured.calls) == 1
    argv = captured.calls[0]["argv"]
    assert argv[0] == str(fake)
    assert argv[1] == "-p"
    assert argv[2] == "test"
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools" in argv
    assert "--dangerously-skip-permissions" not in argv


def test_spawn_captures_stderr_to_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Stderr must go to a file, not DEVNULL, for failure observability."""
    from agents.executor import spawn

    fake = tmp_path / "claude.exe"
    fake.write_text("")

    monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake))

    log_dir = tmp_path / "logs"
    captured = _CapturedPopen()
    result = spawn(
        "test",
        stderr_log_dir=str(log_dir),
        popen=captured,
        probe=_FixedProbe(_healthy_reading()),
    )

    assert result.proc is not None, "spawn should not be throttled"
    assert len(captured.calls) == 1
    stderr_arg = captured.calls[0].get("stderr")
    assert stderr_arg is not None, "stderr must not be DEVNULL"
    assert os.path.basename(stderr_arg.name).startswith("spawn-"), (
        f"stderr file should follow spawn-<ts> convention, got {stderr_arg.name}"
    )
    # The parent file handle must be closed after Popen dup2's the fd —
    # otherwise a long-running scheduler leaks one fd per spawn.
    assert stderr_arg.closed, "parent stderr handle must be closed after spawn"


# ---------------------------------------------------------------------------
# spawn — quota gate
# ---------------------------------------------------------------------------


class _FixedProbe:
    """Probe stub returning a fixed ``UsageReading`` for test control."""

    def __init__(self, reading: Any) -> None:
        self._reading = reading

    def read(self) -> Any:
        return self._reading


def _healthy_reading() -> Any:

    from agents.usage_probe import UsageReading

    return UsageReading(
        limit_window=timedelta(hours=5),
        used=10,
        total=100,
        reset_at=datetime.now(UTC),
        near_exhaustion=False,
    )


def _exhausted_reading() -> Any:

    from agents.usage_probe import UsageReading

    return UsageReading(
        limit_window=timedelta(hours=5),
        used=95,
        total=100,
        reset_at=datetime.now(UTC),
        near_exhaustion=True,
    )


def _false_safe_error_probe() -> _FixedProbe:
    """Probe that raises — confirms the false-safe contract in the executor."""

    class _RaisingProbe:
        def read(self) -> Any:
            raise RuntimeError("probe broken")

    return _RaisingProbe()  # type: ignore[return-value]


def test_spawn_proceeds_when_quota_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Spawn should succeed when the quota probe reports headroom."""
    from agents.executor import spawn

    fake = tmp_path / "claude.exe"
    fake.write_text("")
    monkeypatch.setenv("JARVIS_CLAUDE_BIN", str(fake))

    captured = _CapturedPopen()
    result = spawn(
        "healthy task",
        stderr_log_dir=str(tmp_path / "logs"),
        popen=captured,
        probe=_FixedProbe(_healthy_reading()),
    )

    assert not result.throttled
    assert result.proc is not None
    assert result.reason is None
    assert len(captured.calls) == 1


def test_spawn_refused_when_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Spawn should be refused when the probe reports near-exhaustion."""
    from agents.executor import spawn

    captured = _CapturedPopen()
    result = spawn(
        "exhausted task",
        stderr_log_dir=str(tmp_path / "logs"),
        popen=captured,
        probe=_FixedProbe(_exhausted_reading()),
    )

    assert result.throttled
    assert result.proc is None
    assert result.reason is not None
    assert "near-exhaustion" in result.reason
    # Popen must NOT have been called
    assert len(captured.calls) == 0


def test_spawn_probe_error_returns_false_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A probe error must be treated as near-exhaustion (false-safe)."""
    from agents.executor import spawn

    captured = _CapturedPopen()
    result = spawn(
        "broken probe task",
        stderr_log_dir=str(tmp_path / "logs"),
        popen=captured,
        probe=_false_safe_error_probe(),
    )

    assert result.throttled
    assert result.proc is None
    assert result.reason is not None
    assert len(captured.calls) == 0
