"""Tests for scripts/lib/env_sync.py (#1312).

Covers AC#1 (check/heal core), AC#2 (lock reclaim), and the registry slice
of AC#8's meta-test (probe_modules coverage of third-party imports across
the three MCP server files).
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))

import env_sync  # noqa: E402


def _make_env(tmp_path, probe_modules=("modA", "modB")):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("modA>=1,<2\nmodB>=1,<2\n", encoding="utf-8")
    return env_sync.ManagedEnv(
        name="test-env",
        venv_python=tmp_path / "venv-python",
        manifest=manifest,
        stamp_path=tmp_path / ".deps-stamp",
        probe_modules=probe_modules,
    )


class FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _probes_all_ok(*args, **kwargs):
    return FakeCompleted(0)


def _probes_all_fail(*args, **kwargs):
    return FakeCompleted(1)


def _probes_fail_for(bad_modules):
    def _run(cmd, *args, **kwargs):
        # cmd = [python_exe, "-c", "import <module>"]
        src = cmd[2]
        module = src.split("import ", 1)[1]
        return FakeCompleted(1 if module in bad_modules else 0)

    return _run


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------


def test_check_no_stamp_reports_drift(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_all_ok)
    result = env_sync.check(env)
    assert result.in_sync is False
    assert result.reason == "hash_mismatch"


def test_check_matching_stamp_and_healthy_probes_is_in_sync(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    env.stamp_path.write_text(env_sync._manifest_hash(env.manifest), encoding="utf-8")
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_all_ok)
    result = env_sync.check(env)
    assert result.in_sync is True
    assert result.reason is None


def test_check_manifest_changed_after_stamp_reports_drift(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    env.stamp_path.write_text(env_sync._manifest_hash(env.manifest), encoding="utf-8")
    env.manifest.write_text("modA>=2,<3\nmodB>=1,<2\n", encoding="utf-8")
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_all_ok)
    result = env_sync.check(env)
    assert result.in_sync is False
    assert result.reason == "hash_mismatch"


def test_check_hash_matches_but_probe_fails_reports_drift(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    env.stamp_path.write_text(env_sync._manifest_hash(env.manifest), encoding="utf-8")
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_fail_for({"modB"}))
    result = env_sync.check(env)
    assert result.in_sync is False
    assert result.reason == "probe_failed:modB"


# ---------------------------------------------------------------------------
# heal()
# ---------------------------------------------------------------------------


class FakePopen:
    """Fake subprocess.Popen standing in for the pip install child."""

    def __init__(self, returncode=0, hang=False):
        self._returncode = returncode
        self._hang = hang
        self.pid = 4242
        self.calls = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append((cmd, args, kwargs))
        return self

    def wait(self, timeout=None):
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="pip", timeout=timeout)
        return self._returncode

    def kill(self):
        pass


def test_heal_writes_stamp_on_pip_success_and_probe_pass(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync.subprocess, "Popen", FakePopen(returncode=0))
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_all_ok)
    monkeypatch.setattr(env_sync, "_LOG_DIR", tmp_path / "logs")

    result = env_sync.heal(env)

    assert result.success is True
    assert env.stamp_path.exists()
    assert env.stamp_path.read_text(encoding="utf-8").strip() == env_sync._manifest_hash(
        env.manifest
    )


def test_heal_does_not_write_stamp_when_pip_fails(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync.subprocess, "Popen", FakePopen(returncode=1))
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_all_ok)
    monkeypatch.setattr(env_sync, "_LOG_DIR", tmp_path / "logs")

    result = env_sync.heal(env)

    assert result.success is False
    assert result.reason == "pip_failed"
    assert not env.stamp_path.exists()


def test_heal_does_not_write_stamp_when_post_install_probe_fails(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync.subprocess, "Popen", FakePopen(returncode=0))
    monkeypatch.setattr(env_sync.subprocess, "run", _probes_fail_for({"modA"}))
    monkeypatch.setattr(env_sync, "_LOG_DIR", tmp_path / "logs")

    result = env_sync.heal(env)

    assert result.success is False
    assert result.reason == "probe_failed:modA"
    assert not env.stamp_path.exists()


def test_heal_never_raises_on_internal_error(tmp_path, monkeypatch):
    env = _make_env(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(env_sync.subprocess, "Popen", _boom)
    monkeypatch.setattr(env_sync, "_LOG_DIR", tmp_path / "logs")

    result = env_sync.heal(env)

    assert result.success is False
    assert result.reason.startswith("exception:")


# ---------------------------------------------------------------------------
# Lock reclaim (AC#2)
# ---------------------------------------------------------------------------


def test_acquire_lock_succeeds_when_no_lock_present(tmp_path):
    lock_path = tmp_path / "x.lock"
    assert env_sync._acquire_lock(lock_path) is True
    assert lock_path.exists()


def test_acquire_lock_blocked_by_live_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "x.lock"
    lock_path.write_text(json.dumps({"pid": 99999, "ts": time.time()}), encoding="utf-8")
    monkeypatch.setattr(env_sync, "_pid_alive", lambda pid: True)
    assert env_sync._acquire_lock(lock_path) is False


def test_acquire_lock_reclaims_dead_pid(tmp_path, monkeypatch):
    lock_path = tmp_path / "x.lock"
    lock_path.write_text(json.dumps({"pid": 99999, "ts": time.time()}), encoding="utf-8")
    monkeypatch.setattr(env_sync, "_pid_alive", lambda pid: False)
    assert env_sync._acquire_lock(lock_path) is True


def test_acquire_lock_reclaims_expired_ttl(tmp_path, monkeypatch):
    lock_path = tmp_path / "x.lock"
    stale_ts = time.time() - (env_sync.LOCK_TTL_SECONDS + 10)
    lock_path.write_text(json.dumps({"pid": os.getpid(), "ts": stale_ts}), encoding="utf-8")
    monkeypatch.setattr(env_sync, "_pid_alive", lambda pid: True)
    assert env_sync._acquire_lock(lock_path) is True


def test_heal_returns_locked_when_second_caller_blocked(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync, "_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(env_sync, "_acquire_lock", lambda lock_path, ttl=None: False)

    result = env_sync.heal(env)

    assert result.success is False
    assert result.reason == "locked"


# ---------------------------------------------------------------------------
# Registry meta-test (AC#8): probe_modules must cover every third-party
# top-level import across the three MCP server files. This is the standing
# guard against the nest_asyncio/telethon class of silent drift recurring.
# ---------------------------------------------------------------------------

_STDLIB_ALLOWLIST = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()

_SERVER_FILES = [
    _REPO_ROOT / "mcp-memory" / "server.py",
    _REPO_ROOT / "mcp-status" / "server.py",
    _REPO_ROOT / "scripts" / "telegram-mcp-server.py",
]

# Local first-party packages that show up as top-level imports but are not
# pip-installed third-party dependencies (repo-local modules).
_FIRST_PARTY_ALLOWLIST = {"scripts", "mcp_memory", "mcp_status", "client", "embeddings", "handlers"}


def _is_sibling_module(name: str, source_dir: Path) -> bool:
    return (source_dir / f"{name}.py").exists() or (source_dir / name / "__init__.py").exists()


def _top_level_third_party_imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import, always first-party
            if node.module:
                found.add(node.module.split(".")[0])
    return {
        m
        for m in found
        if m not in _STDLIB_ALLOWLIST
        and m not in _FIRST_PARTY_ALLOWLIST
        and not _is_sibling_module(m, path.parent)
    }


def test_registry_probe_modules_cover_all_server_third_party_imports():
    main_env = env_sync.get_env("main")
    assert main_env is not None

    required = set()
    for server_file in _SERVER_FILES:
        required |= _top_level_third_party_imports(server_file)

    missing = required - set(main_env.probe_modules)
    assert not missing, f"probe_modules missing third-party imports: {missing}"
