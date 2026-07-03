"""Unit tests for scripts/session-context.py mirror drift detection (#753).

Covers the drift-detection predicate:
  - Missing version marker → no warning
  - Installed SHA matches HEAD → no warning
  - Installed SHA differs from HEAD → warning with SHAs + commit count
  - Git unavailable → graceful no-op (no crash, no warning)
  - Empty marker file → graceful no-op
  - Same session-context scope as test_milestone_sweep.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


# Stub optional deps so module import succeeds without them installed.
# Note: supabase/ dir in repo root creates a namespace package already in
# sys.modules — we must add create_client to the existing module rather than
# replace it (same approach as test_milestone_sweep.py).
for _stub in ("dotenv", "supabase"):
    mod = sys.modules.setdefault(_stub, types.ModuleType(_stub))
    if _stub == "dotenv":
        mod.load_dotenv = lambda *a, **k: None
    if _stub == "supabase" and not hasattr(mod, "create_client"):
        mod.create_client = MagicMock()

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "session-context.py"
_spec = importlib.util.spec_from_file_location("session_context_drift", _PATH)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _write_marker(home_dir: Path, sha: str) -> Path:
    """Write a .jarvis-version marker at the canonical path."""
    marker = home_dir / ".claude" / ".jarvis-version"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(sha + "\n", encoding="utf-8")
    return marker


# ---------------------------------------------------------------------------
# Marker absent
# ---------------------------------------------------------------------------


def test_mirror_drift_none_when_marker_missing():
    """No .jarvis-version file → no warning."""
    result = sc._check_mirror_drift(
        version_path=Path("/nonexistent/path/.jarvis-version"),
        repo_root=Path("/mock/repo"),
    )
    assert result is None


def test_mirror_drift_none_when_marker_empty(monkeypatch, tmp_path):
    """Empty marker file → treat as absent → no warning."""
    marker = _write_marker(tmp_path, "")
    marker.write_text("", encoding="utf-8")
    result = sc._check_mirror_drift(
        version_path=marker,
        repo_root=tmp_path,
    )
    assert result is None


def test_mirror_drift_none_when_marker_whitespace_only(monkeypatch, tmp_path):
    """Whitespace-only marker → treat as absent → no warning."""
    marker = _write_marker(tmp_path, "  \n  ")
    result = sc._check_mirror_drift(
        version_path=marker,
        repo_root=tmp_path,
    )
    assert result is None


# ---------------------------------------------------------------------------
# In sync
# ---------------------------------------------------------------------------


def test_mirror_drift_none_when_in_sync(tmp_path):
    """Installed SHA matches HEAD → no warning."""
    marker = _write_marker(tmp_path, "deadbeef1234567")
    head_sha = "deadbeef1234567"

    with patch.object(sc.subprocess, "run") as mock_run:
        mock_run.return_value = MagicMock(stdout=head_sha + "\n")

        result = sc._check_mirror_drift(
            version_path=marker,
            repo_root=tmp_path,
        )

    assert result is None
    # Only the rev-parse call — no rev-list needed since SHAs match
    assert mock_run.call_count == 1
    assert "rev-parse" in mock_run.call_args[0][0]


# ---------------------------------------------------------------------------
# Behind
# ---------------------------------------------------------------------------


def test_mirror_drift_warning_when_behind(tmp_path):
    """Installed SHA ≠ HEAD → warning with SHAs and commit count."""
    marker = _write_marker(tmp_path, "oldsha1234567")
    head_sha = "newsha890abcd"

    with patch.object(sc.subprocess, "run") as mock_run:
        # First call: rev-parse HEAD, second call: rev-list --count
        mock_run.side_effect = [
            MagicMock(stdout=head_sha + "\n"),
            MagicMock(stdout="5\n"),
        ]

        result = sc._check_mirror_drift(
            version_path=marker,
            repo_root=tmp_path,
        )

    assert result is not None
    assert "Mirror Drift" in result
    assert "oldsha123456" in result  # first 12 chars
    assert "newsha890abc" in result  # first 12 chars
    assert "5" in result
    assert "install.ps1" in result or "install.sh" in result


def test_mirror_drift_warning_unknown_count_on_rev_list_failure(tmp_path):
    """When rev-list fails, commit count shows '?' — warning still emitted."""
    marker = _write_marker(tmp_path, "oldsha1234567")
    head_sha = "newsha890abcd"

    with patch.object(sc.subprocess, "run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout=head_sha + "\n"),
            subprocess.CalledProcessError(1, ["git", "rev-list"]),
        ]

        result = sc._check_mirror_drift(
            version_path=marker,
            repo_root=tmp_path,
        )

    assert result is not None
    assert "?" in result
    assert "oldsha123456" in result  # first 12 chars
    assert "newsha890abc" in result  # first 12 chars


# ---------------------------------------------------------------------------
# Git unavailable
# ---------------------------------------------------------------------------


def test_mirror_drift_none_on_rev_parse_failure(tmp_path):
    """git rev-parse fails (not a git dir) → no warning, no crash."""
    marker = _write_marker(tmp_path, "some-sha")

    with patch.object(sc.subprocess, "run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(128, ["git", "rev-parse"])

        result = sc._check_mirror_drift(
            version_path=marker,
            repo_root=tmp_path,
        )

    assert result is None


def test_mirror_drift_none_when_git_not_installed(tmp_path):
    """git binary not found → no warning, no crash."""
    marker = _write_marker(tmp_path, "some-sha")

    with patch.object(sc.subprocess, "run") as mock_run:
        mock_run.side_effect = FileNotFoundError("git not found")

        result = sc._check_mirror_drift(
            version_path=marker,
            repo_root=tmp_path,
        )

    assert result is None


def test_mirror_drift_none_on_oserror_reading_marker(tmp_path):
    """OSError reading marker (permission denied etc.) → no warning."""
    marker = tmp_path / ".claude" / ".jarvis-version"
    marker.parent.mkdir(parents=True, exist_ok=True)
    # Create marker but make it unreadable
    marker.write_text("some-sha\n", encoding="utf-8")
    marker.chmod(0o000)

    try:
        result = sc._check_mirror_drift(
            version_path=marker,
            repo_root=tmp_path,
        )
        assert result is None
    finally:
        marker.chmod(0o644)
