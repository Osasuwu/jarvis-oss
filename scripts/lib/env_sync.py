"""Dependency drift detection + self-heal for long-lived MCP venvs (#1312).

Stdlib-only by design: this module is imported from scripts/session-context.py
during SessionStart, before any project dependency is guaranteed installed.

check(env) compares the manifest's sha256 against the recorded stamp AND
import-probes env.probe_modules through the venv's own interpreter — hash-only
checks miss the nest_asyncio/pythonjsonlogger/telethon class of bug where a
module is imported by a server file but never declared in the manifest, so
the hash never changes even though the venv is unhealthy.

heal(env) runs `<venv python> -m pip install -r <manifest>` with its own
timeout + tree-kill (see the subprocess_capture_output_grandchild_pipe_hang
memory: capture_output=True + timeout hangs forever if a grandchild inherits
the pipe — this redirects the child's stdout/stderr to a real log file
instead of a PIPE). The stamp is written only when pip exits 0 AND a
follow-up probe passes, so a "successful" but incomplete install never masks
future drift.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HEAL_TIMEOUT = 40

# ceiling: lock reclaim (_acquire_lock) is read-then-write, not atomic —
# fine for a single-developer-machine tool with at most a couple of
# concurrent Claude Code sessions; switch to os.open(O_CREAT|O_EXCL) if this
# ever runs on a shared multi-user host.
LOCK_TTL_SECONDS = 120

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VENV_PYTHON = _REPO_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
_LOG_DIR = _REPO_ROOT / ".claude" / "logs"


@dataclass(frozen=True)
class ManagedEnv:
    name: str
    venv_python: Path
    manifest: Path
    stamp_path: Path
    probe_modules: tuple


@dataclass(frozen=True)
class CheckResult:
    in_sync: bool
    reason: str | None = None
    old_hash: str | None = None
    new_hash: str | None = None


@dataclass(frozen=True)
class HealResult:
    success: bool
    reason: str | None = None
    old_hash: str | None = None
    new_hash: str | None = None
    log_path: Path | None = None


REGISTRY: list[ManagedEnv] = [
    ManagedEnv(
        name="main",
        venv_python=_VENV_PYTHON,
        manifest=_REPO_ROOT / "mcp-memory" / "requirements.txt",
        stamp_path=_REPO_ROOT / ".venv" / ".deps-stamp",
        # Third-party top-level imports across mcp-memory/server.py,
        # mcp-status/server.py, scripts/telegram-mcp-server.py. Keep this in
        # sync with the meta-test in tests/infrastructure/test_env_sync.py —
        # that test fails closed if a server file gains an import this
        # registry doesn't know about.
        probe_modules=(
            "mcp",
            "supabase",
            "voyageai",
            "httpx",
            "dotenv",
            "nest_asyncio",
            "pythonjsonlogger",
            "telethon",
        ),
    ),
]


def get_env(name: str) -> ManagedEnv | None:
    for env in REGISTRY:
        if env.name == name:
            return env
    return None


def _manifest_hash(manifest: Path) -> str:
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _read_stamp(stamp_path: Path) -> str | None:
    try:
        return stamp_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_stamp(stamp_path: Path, digest: str) -> None:
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(digest, encoding="utf-8")


def _probe_import(python_exe: Path, module: str, timeout: int = 15) -> bool:
    try:
        result = subprocess.run(
            [str(python_exe), "-c", f"import {module}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def check(env: ManagedEnv) -> CheckResult:
    new_hash = _manifest_hash(env.manifest)
    old_hash = _read_stamp(env.stamp_path)
    if old_hash != new_hash:
        return CheckResult(False, "hash_mismatch", old_hash, new_hash)
    for module in env.probe_modules:
        if not _probe_import(env.venv_python, module):
            return CheckResult(False, f"probe_failed:{module}", old_hash, new_hash)
    return CheckResult(True, None, old_hash, new_hash)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return True  # can't determine — assume alive, don't reclaim
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_lock(lock_path: Path, ttl: int = LOCK_TTL_SECONDS) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = data.get("pid")
            ts = data.get("ts", 0)
        except (ValueError, OSError):
            pid, ts = None, 0
        stale = (now - ts) > ttl or (pid is not None and not _pid_alive(pid))
        if not stale:
            return False
    try:
        lock_path.write_text(json.dumps({"pid": os.getpid(), "ts": now}), encoding="utf-8")
    except OSError:
        return False
    return True


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass


def _tree_kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )
        return
    try:
        os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, OSError):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _run_pip_install(python_exe: Path, manifest: Path, log_path: Path, timeout: int) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as logf:
        logf.write(f"\n--- env-sync heal {time.time()} ---\n".encode("utf-8"))
        proc = subprocess.Popen(
            [str(python_exe), "-m", "pip", "install", "-r", str(manifest)],
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _tree_kill(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return -1


def heal(env: ManagedEnv, timeout: int = DEFAULT_HEAL_TIMEOUT) -> HealResult:
    log_path = _LOG_DIR / "env-sync.log"
    try:
        lock_path = env.stamp_path.parent / f".{env.name}.env-sync.lock"
        if not _acquire_lock(lock_path):
            return HealResult(False, "locked", log_path=log_path)
        try:
            old_hash = _read_stamp(env.stamp_path)
            rc = _run_pip_install(env.venv_python, env.manifest, log_path, timeout)
            if rc != 0:
                return HealResult(False, "pip_failed", old_hash, log_path=log_path)
            new_hash = _manifest_hash(env.manifest)
            for module in env.probe_modules:
                if not _probe_import(env.venv_python, module):
                    return HealResult(False, f"probe_failed:{module}", old_hash, new_hash, log_path)
            _write_stamp(env.stamp_path, new_hash)
            return HealResult(True, None, old_hash, new_hash, log_path)
        finally:
            _release_lock(lock_path)
    except Exception as exc:  # noqa: BLE001 - heal() must never raise
        return HealResult(False, f"exception:{exc}", log_path=log_path)
