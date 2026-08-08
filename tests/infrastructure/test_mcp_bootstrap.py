"""Tests for scripts/lib/mcp_bootstrap.py (#1312 AC#4).

Covers: child stderr redirected to .claude/logs/mcp-<name>.stderr.log instead
of inherited; child stdout left untouched (it's the JSON-RPC transport);
non-zero child exit appends one breadcrumb line to .claude/mcp-failures.jsonl
matching the schema scripts/session-context.py::_check_mcp_failures consumes;
no pip invocation anywhere in the three launcher scripts (regression guard).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))

import mcp_bootstrap  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


class FakeRun:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return FakeCompleted(self.returncode)


def test_run_server_tracked_redirects_stderr_to_log_file(tmp_path):
    fake_run = FakeRun(returncode=0)
    mcp_bootstrap.run_server_tracked(
        "memory", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )

    assert len(fake_run.calls) == 1
    _, kwargs = fake_run.calls[0]
    assert kwargs["stderr"] is not None
    assert kwargs["stderr"] != sys.stderr

    log_path = tmp_path / ".claude" / "logs" / "mcp-memory.stderr.log"
    assert log_path.exists()


def test_run_server_tracked_leaves_stdout_untouched(tmp_path):
    fake_run = FakeRun(returncode=0)
    mcp_bootstrap.run_server_tracked(
        "status", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )

    _, kwargs = fake_run.calls[0]
    assert kwargs["stdout"] is None


def test_run_server_tracked_returns_child_returncode(tmp_path):
    fake_run = FakeRun(returncode=7)
    rc = mcp_bootstrap.run_server_tracked(
        "telegram", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )
    assert rc == 7


def test_run_server_tracked_records_failure_on_nonzero_exit(tmp_path):
    fake_run = FakeRun(returncode=1)
    now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
    mcp_bootstrap.run_server_tracked(
        "memory", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run, _now=now
    )

    failures_path = tmp_path / ".claude" / "mcp-failures.jsonl"
    assert failures_path.exists()
    entry = json.loads(failures_path.read_text(encoding="utf-8").strip())
    assert entry == {
        "server": "memory",
        "timestamp": now.isoformat(),
        "exit_code": 1,
        "stderr_tail": "",
    }


def test_run_server_tracked_includes_stderr_tail_on_failure(tmp_path):
    class WritingFakeRun(FakeRun):
        def __call__(self, cmd, **kwargs):
            kwargs["stderr"].write(b"boom: traceback line\n")
            return super().__call__(cmd, **kwargs)

    fake_run = WritingFakeRun(returncode=1)
    mcp_bootstrap.run_server_tracked(
        "memory", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )

    failures_path = tmp_path / ".claude" / "mcp-failures.jsonl"
    entry = json.loads(failures_path.read_text(encoding="utf-8").strip())
    assert entry["stderr_tail"] == "boom: traceback line\n"


def test_run_server_tracked_bounds_stderr_log_size(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_bootstrap, "_MAX_LOG_BYTES", 100)
    log_path = tmp_path / ".claude" / "logs" / "mcp-memory.stderr.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_bytes(b"x" * 200)

    fake_run = FakeRun(returncode=0)
    mcp_bootstrap.run_server_tracked(
        "memory", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )

    assert log_path.stat().st_size < 200


def test_run_server_tracked_no_failure_recorded_on_zero_exit(tmp_path):
    fake_run = FakeRun(returncode=0)
    mcp_bootstrap.run_server_tracked(
        "memory", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )

    failures_path = tmp_path / ".claude" / "mcp-failures.jsonl"
    assert not failures_path.exists()


def test_run_server_tracked_survives_unwritable_log_dir(tmp_path, monkeypatch):
    def _boom(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(mcp_bootstrap.Path, "mkdir", _boom)
    fake_run = FakeRun(returncode=0)

    rc = mcp_bootstrap.run_server_tracked(
        "memory", "python-exe", "server.py", tmp_path, _subprocess_run=fake_run
    )

    assert rc == 0
    _, kwargs = fake_run.calls[0]
    assert kwargs["stderr"] is None


# ---------------------------------------------------------------------------
# Static regression guards on the three launcher scripts themselves
# ---------------------------------------------------------------------------

_LAUNCHERS = [
    _REPO_ROOT / "scripts" / "run-memory-server.py",
    _REPO_ROOT / "scripts" / "run-status-server.py",
    _REPO_ROOT / "scripts" / "run-telegram-mcp.py",
]


def test_launchers_never_invoke_pip():
    for launcher in _LAUNCHERS:
        source = launcher.read_text(encoding="utf-8")
        assert "pip" not in source, f"{launcher} must never run pip (AC#4)"


def test_launchers_use_run_server_tracked():
    for launcher in _LAUNCHERS:
        source = launcher.read_text(encoding="utf-8")
        assert "run_server_tracked" in source, f"{launcher} must delegate to mcp_bootstrap"
