"""Dependency drift detection + self-heal for long-lived MCP venvs (#1312, #1313).

Stdlib-only by design: this module is imported from scripts/session-context.py
during SessionStart, before any project dependency is guaranteed installed.

check(env) compares the lockfile's (or manifest's) sha256 against the recorded stamp AND
import-probes env.probe_modules through the venv's own interpreter — hash-only
checks miss the nest_asyncio/pythonjsonlogger/telethon class of bug where a
module is imported by a server file but never declared in the manifest, so
the hash never changes even though the venv is unhealthy.

heal(env) uses uv to sync from the lockfile if present (#1313), otherwise falls back
to `<venv python> -m pip install -r <manifest>` (#1312). Both use their own
timeout + tree-kill (see the subprocess_capture_output_grandchild_pipe_hang
memory: capture_output=True + timeout hangs forever if a grandchild inherits
the pipe — this redirects the child's stdout/stderr to a real log file
instead of a PIPE). The stamp is written only when install exits 0 AND a
follow-up probe passes, so a "successful" but incomplete install never masks
future drift.

On Windows, a sibling MCP server process (from another Claude Code session
sharing this repo's root .venv) can still have a native extension DLL (e.g.
pydantic_core) loaded when heal() runs, so uv/pip fail with "os error 5" /
"Access is denied" while trying to replace it — a lock conflict, not a real
dependency error. The existing lock file (_acquire_lock) only serializes
concurrent heal() *calls*; it does nothing about a live server process just
sitting on the DLL. heal() retries the install a few times with backoff when
the failure output matches that specific signature (#1713), so a transient
conflict (e.g. the sibling session cycling its subprocess) has a chance to
clear before heal() gives up and reports failure.
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

# Signatures of a Windows sharing-violation ("file in use by another
# process") failure, as they appear in uv/pip stdout+stderr (#1713). Distinct
# from a real dependency error — the install itself is fine, something else
# just has the file open right now.
_FILE_LOCK_SIGNATURES = (
    "os error 5",
    "OSError: [WinError 5]",
    "Отказано в доступе",
)

# Delays (seconds) before each install attempt; first attempt is immediate.
# Only consulted when the previous attempt's failure matched a lock
# signature above — a non-lock failure never retries.
_LOCK_RETRY_DELAYS = (0, 2, 4)

_sleep = time.sleep  # module-level indirection so tests can stub out the wait

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
    lockfile: Path | None = None  # (#1313) uv.lock for reproducible installs


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
        # mcp-memory/{requirements.txt,uv.lock} were retired along with the
        # rest of the memory stack (#1801); the vendored Telegram MCP server
        # (the only other consumer of this env) was retired in #1802. The
        # root project still carries every extra directly, so this env
        # tracks the root pyproject.toml/uv.lock.
        manifest=_REPO_ROOT / "pyproject.toml",
        stamp_path=_REPO_ROOT / ".venv" / ".deps-stamp",
        lockfile=_REPO_ROOT / "uv.lock",  # (#1313)
        probe_modules=("dotenv",),
    ),
]


def get_env(name: str) -> ManagedEnv | None:
    for env in REGISTRY:
        if env.name == name:
            return env
    return None


def _manifest_hash(manifest: Path) -> str:
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _combined_hash(manifest: Path, lockfile: Path) -> str:
    """Hash both manifest and lockfile together for drift detection (#1313).

    When both files exist, changes to either the manifest (dependency ranges)
    or lockfile (resolved versions) are detected as drift. This ensures that
    if Dependabot updates the manifest, the missing lockfile regeneration is
    caught as drift (manifest hash != lockfile hash hash combination).
    """
    manifest_bytes = manifest.read_bytes()
    lockfile_bytes = lockfile.read_bytes()
    combined = manifest_bytes + lockfile_bytes
    return hashlib.sha256(combined).hexdigest()


def _get_hash_source(env: ManagedEnv) -> Path:
    """Return the file to hash for drift detection (#1313).

    Prefers lockfile if it exists, falls back to manifest. This ensures
    that when a lockfile is added, drift is detected and re-healing uses
    the lockfile (not stale manifest range). When a lockfile is absent
    (legacy setup), uses manifest for backward compatibility.
    """
    if env.lockfile and env.lockfile.exists():
        return env.lockfile
    return env.manifest


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
    # When both manifest and lockfile exist, hash both to detect drift in either
    if env.lockfile and env.lockfile.exists() and env.manifest.exists():
        new_hash = _combined_hash(env.manifest, env.lockfile)
    else:
        hash_source = _get_hash_source(env)
        new_hash = _manifest_hash(hash_source)
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


def _run_uv_sync(env_dir: Path, venv_python: Path, log_path: Path, timeout: int) -> int:
    """Run `uv sync` in the given project directory, targeting the tracked venv (#1313, #1538).

    `uv sync --project <dir>` has no flag to redirect its target venv — left
    alone it manages `<dir>/.venv`, not the shared venv the rest of the
    codebase (and the post-heal probe below) actually uses. The only override
    is the UV_PROJECT_ENVIRONMENT env var (astral-sh/uv#20060 confirms there's
    still no equivalent CLI flag), pointed at venv_python's venv root.

    `--all-extras` is required post-#1801: the packages this env actually
    probes for (mcp, nest_asyncio, pythonjsonlogger, telethon) now live behind
    the root project's `telegram` extra rather than as plain base
    dependencies (unlike the old, now-retired mcp-memory sub-project, which
    declared them unconditionally) — a plain `uv sync --project .` installs
    only the base `dependencies` list and would leave them missing.

    `--inexact` is required alongside it: the shared venv also carries extras
    this specific heal has no business touching if something upstream (e.g.
    a `pip install` outside uv's view) put an unrelated package there.
    Without `--inexact`, `uv sync` treats its own resolved set as the venv's
    *complete* target and removes everything outside it — this bit the
    project live on 2026-09-01, when a routine MCP server restart's self-heal
    (at the time scoped to the mcp-memory sub-project only, before the
    `--all-extras` root-project switch) silently uninstalled psycopg from
    underneath a live wake_driver. `--all-extras` closes the original gap
    (every extra, including `agents`, is now part of this env's own sync
    target) and `--inexact` remains as defense in depth.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sync_env = dict(os.environ)
    sync_env["UV_PROJECT_ENVIRONMENT"] = str(venv_python.parent.parent)
    with open(log_path, "ab") as logf:
        logf.write(f"\n--- env-sync heal (uv sync) {time.time()} ---\n".encode("utf-8"))
        proc = subprocess.Popen(
            ["uv", "sync", "--project", str(env_dir), "--all-extras", "--inexact"],
            cwd=str(env_dir),
            env=sync_env,
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


def _run_pip_install(python_exe: Path, manifest: Path, log_path: Path, timeout: int) -> int:
    """Run `pip install -r <manifest>` for backward compatibility (#1312)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as logf:
        logf.write(f"\n--- env-sync heal (pip install) {time.time()} ---\n".encode("utf-8"))
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


def _is_file_lock_error(log_path: Path, since_offset: int) -> bool:
    """Check whether the log content written since `since_offset` looks like
    a Windows file-lock sharing violation rather than a real install error."""
    try:
        with open(log_path, "rb") as f:
            f.seek(since_offset)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return any(sig in tail for sig in _FILE_LOCK_SIGNATURES)


def _run_with_lock_retry(run_once, log_path: Path) -> tuple[int, bool]:
    """Run `run_once()` (an install attempt returning an rc), retrying with
    backoff only while each failure's log output matches a file-lock
    signature (#1713). Returns (final_rc, exhausted_as_lock) — the latter is
    True only when every attempt failed AND the last one was a lock error, so
    the caller can report a distinct, actionable reason instead of the
    generic "install failed".
    """
    rc = 1
    was_lock_error = False
    for delay in _LOCK_RETRY_DELAYS:
        if delay:
            _sleep(delay)
        offset = log_path.stat().st_size if log_path.exists() else 0
        rc = run_once()
        if rc == 0:
            return rc, False
        was_lock_error = _is_file_lock_error(log_path, offset)
        if not was_lock_error:
            return rc, False
    return rc, was_lock_error


def heal(env: ManagedEnv, timeout: int = DEFAULT_HEAL_TIMEOUT) -> HealResult:
    log_path = _LOG_DIR / "env-sync.log"
    try:
        lock_path = env.stamp_path.parent / f".{env.name}.env-sync.lock"
        if not _acquire_lock(lock_path):
            return HealResult(False, "locked", log_path=log_path)
        try:
            old_hash = _read_stamp(env.stamp_path)
            # Use uv sync if lockfile exists (#1313), else pip install (#1312)
            use_uv = env.lockfile and env.lockfile.exists()
            if use_uv:
                env_project_dir = env.lockfile.parent
                rc, lock_exhausted = _run_with_lock_retry(
                    lambda: _run_uv_sync(env_project_dir, env.venv_python, log_path, timeout),
                    log_path,
                )
                install_method = "uv_sync"
            else:
                rc, lock_exhausted = _run_with_lock_retry(
                    lambda: _run_pip_install(env.venv_python, env.manifest, log_path, timeout),
                    log_path,
                )
                install_method = "pip_install"

            if rc != 0:
                reason = (
                    "file_locked_after_retries" if lock_exhausted else f"{install_method}_failed"
                )
                return HealResult(False, reason, old_hash, log_path=log_path)

            # Compute new hash using same logic as check()
            if env.lockfile and env.lockfile.exists() and env.manifest.exists():
                new_hash = _combined_hash(env.manifest, env.lockfile)
            else:
                hash_source = _get_hash_source(env)
                new_hash = _manifest_hash(hash_source)
            for module in env.probe_modules:
                if not _probe_import(env.venv_python, module):
                    return HealResult(False, f"probe_failed:{module}", old_hash, new_hash, log_path)
            _write_stamp(env.stamp_path, new_hash)
            return HealResult(True, None, old_hash, new_hash, log_path)
        finally:
            _release_lock(lock_path)
    except Exception as exc:  # noqa: BLE001 - heal() must never raise
        return HealResult(False, f"exception:{exc}", log_path=log_path)
