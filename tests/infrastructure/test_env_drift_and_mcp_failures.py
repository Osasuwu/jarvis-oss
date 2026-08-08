"""Unit tests for scripts/session-context.py env-drift + MCP-failure surfacing (#1312).

Covers:
  - AC#3: session-context.py calls env_sync.check(); on drift calls heal();
    injects a visible warning block (healed-with-delta or
    heal-failed-with-remediation); silent when in sync; never raises.
  - AC#5: session-context.py surfaces .claude/mcp-failures.jsonl breadcrumbs
    newer than 24h, with no duplicate re-reporting across runs.

Same import-stub approach as test_mirror_drift.py / test_milestone_sweep.py —
supabase/dotenv stubbed so the module imports without real deps installed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

for _stub in ("dotenv", "supabase"):
    mod = sys.modules.setdefault(_stub, types.ModuleType(_stub))
    if _stub == "dotenv":
        mod.load_dotenv = lambda *a, **k: None
    if _stub == "supabase" and not hasattr(mod, "create_client"):
        mod.create_client = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PATH = _REPO_ROOT / "scripts" / "session-context.py"
_spec = importlib.util.spec_from_file_location("session_context_env_drift", _PATH)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))
import env_sync  # noqa: E402


def _make_env(tmp_path, probe_modules=("modA",)):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("modA>=1,<2\n", encoding="utf-8")
    return env_sync.ManagedEnv(
        name="main",
        venv_python=tmp_path / "venv-python",
        manifest=manifest,
        stamp_path=tmp_path / ".deps-stamp",
        probe_modules=probe_modules,
    )


# ---------------------------------------------------------------------------
# AC#3: _check_env_drift
# ---------------------------------------------------------------------------


def test_env_drift_none_when_in_sync(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    env.stamp_path.write_text(env_sync._manifest_hash(env.manifest), encoding="utf-8")
    monkeypatch.setattr(env_sync, "get_env", lambda name: env)
    monkeypatch.setattr(env_sync, "check", lambda e: env_sync.CheckResult(True, None, "h", "h"))
    monkeypatch.setattr(sc, "env_sync", env_sync, raising=False)

    result = sc._check_env_drift(_env_sync_module=env_sync)
    assert result is None


def test_env_drift_warning_when_healed(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync, "get_env", lambda name: env)
    monkeypatch.setattr(
        env_sync,
        "check",
        lambda e: env_sync.CheckResult(False, "hash_mismatch", "oldhash1234", "newhash5678"),
    )
    monkeypatch.setattr(
        env_sync,
        "heal",
        lambda e, timeout=40: env_sync.HealResult(
            True, None, "oldhash1234", "newhash5678", tmp_path / "log.log"
        ),
    )

    result = sc._check_env_drift(_env_sync_module=env_sync)

    assert result is not None
    assert "Healed" in result
    assert "oldhash1234"[:12] in result
    assert "newhash5678"[:12] in result


def test_env_drift_warning_when_heal_fails(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr(env_sync, "get_env", lambda name: env)
    monkeypatch.setattr(
        env_sync,
        "check",
        lambda e: env_sync.CheckResult(False, "probe_failed:modA", "oldhash1234", "oldhash1234"),
    )
    monkeypatch.setattr(
        env_sync,
        "heal",
        lambda e, timeout=40: env_sync.HealResult(
            False, "pip_failed", "oldhash1234", None, tmp_path / "log.log"
        ),
    )

    result = sc._check_env_drift(_env_sync_module=env_sync)

    assert result is not None
    assert "Heal Failed" in result
    assert "pip_failed" in result
    assert str(env.venv_python) in result
    assert str(env.manifest) in result


def test_env_drift_none_when_registry_entry_missing(monkeypatch):
    monkeypatch.setattr(env_sync, "get_env", lambda name: None)
    result = sc._check_env_drift(_env_sync_module=env_sync)
    assert result is None


def test_env_drift_never_raises_on_internal_error(monkeypatch):
    def _boom(name):
        raise RuntimeError("boom")

    monkeypatch.setattr(env_sync, "get_env", _boom)
    result = sc._check_env_drift(_env_sync_module=env_sync)
    assert result is None


# ---------------------------------------------------------------------------
# AC#5: _check_mcp_failures
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def test_mcp_failures_none_when_file_missing(tmp_path):
    result = sc._check_mcp_failures(
        failures_path=tmp_path / "mcp-failures.jsonl",
        reported_path=tmp_path / "mcp-failures.reported.json",
    )
    assert result is None


def test_mcp_failures_reports_recent_entry(tmp_path):
    now = datetime.now(timezone.utc)
    failures = tmp_path / "mcp-failures.jsonl"
    _write_jsonl(
        failures,
        [
            {"server": "memory", "timestamp": now.isoformat(), "exit_code": 1},
        ],
    )

    result = sc._check_mcp_failures(
        failures_path=failures,
        reported_path=tmp_path / "mcp-failures.reported.json",
        _now=now,
    )

    assert result is not None
    assert "memory" in result
    assert "MCP Bootstrap Failures" in result


def test_mcp_failures_ignores_entries_older_than_24h(tmp_path):
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(hours=30)).isoformat()
    failures = tmp_path / "mcp-failures.jsonl"
    _write_jsonl(
        failures,
        [
            {"server": "status", "timestamp": old_ts, "exit_code": 1},
        ],
    )

    result = sc._check_mcp_failures(
        failures_path=failures,
        reported_path=tmp_path / "mcp-failures.reported.json",
        _now=now,
    )

    assert result is None


def test_mcp_failures_no_duplicate_reporting_across_runs(tmp_path):
    now = datetime.now(timezone.utc)
    failures = tmp_path / "mcp-failures.jsonl"
    reported = tmp_path / "mcp-failures.reported.json"
    _write_jsonl(
        failures,
        [
            {"server": "telegram", "timestamp": now.isoformat(), "exit_code": 2},
        ],
    )

    first = sc._check_mcp_failures(failures_path=failures, reported_path=reported, _now=now)
    assert first is not None
    assert "telegram" in first

    second = sc._check_mcp_failures(failures_path=failures, reported_path=reported, _now=now)
    assert second is None


def test_mcp_failures_reports_only_new_entry_when_one_already_reported(tmp_path):
    now = datetime.now(timezone.utc)
    failures = tmp_path / "mcp-failures.jsonl"
    reported = tmp_path / "mcp-failures.reported.json"

    _write_jsonl(
        failures,
        [
            {"server": "memory", "timestamp": now.isoformat(), "exit_code": 1},
        ],
    )
    first = sc._check_mcp_failures(failures_path=failures, reported_path=reported, _now=now)
    assert first is not None

    later = now + timedelta(minutes=5)
    _write_jsonl(
        failures,
        [
            {"server": "memory", "timestamp": now.isoformat(), "exit_code": 1},
            {"server": "status", "timestamp": later.isoformat(), "exit_code": 3},
        ],
    )
    second = sc._check_mcp_failures(failures_path=failures, reported_path=reported, _now=later)

    assert second is not None
    assert "status" in second
    assert "memory" not in second


def test_mcp_failures_none_on_malformed_json_lines(tmp_path):
    now = datetime.now(timezone.utc)
    failures = tmp_path / "mcp-failures.jsonl"
    failures.parent.mkdir(parents=True, exist_ok=True)
    failures.write_text("not json\n\n{broken\n", encoding="utf-8")

    result = sc._check_mcp_failures(
        failures_path=failures,
        reported_path=tmp_path / "mcp-failures.reported.json",
        _now=now,
    )
    assert result is None
