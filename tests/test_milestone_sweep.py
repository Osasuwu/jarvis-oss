"""Tests for _check_milestone_sweep in scripts/session-context.py (issue #605).

Tests the milestone-close detection logic using mocked subprocess.run calls.

The tested module has a hyphen in its filename so it's loaded via importlib
rather than a regular import statement.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_MODULE_PATH = _SCRIPTS_DIR / "session-context.py"

# Stub optional deps before exec_module so collection works without them installed.
_dotenv_stub = types.ModuleType("dotenv")
_dotenv_stub.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", _dotenv_stub)

_supabase_mod = sys.modules.setdefault("supabase", types.ModuleType("supabase"))
if not hasattr(_supabase_mod, "create_client"):
    _supabase_mod.create_client = MagicMock()

# Load session-context.py via importlib (hyphen in filename prevents normal import).
_spec = importlib.util.spec_from_file_location("session_context", _MODULE_PATH)
_session_context = importlib.util.module_from_spec(_spec)
_sys_path_restore = list(sys.path)
sys.path.insert(0, str(_REPO_ROOT))
_spec.loader.exec_module(_session_context)
sys.path = _sys_path_restore

# The function under test.
_check_milestone_sweep = _session_context._check_milestone_sweep

# Frozen "now" so date arithmetic doesn't drift as real time passes.
_NOW = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MILESTONE_WITHIN_WINDOW = {
    "number": 42,
    "title": "Test Milestone",
    "closed_at": "2026-05-18T10:00:00Z",
    "closed_issues": 5,
    "open_issues": 0,
}

_MILESTONE_OLD = {
    "number": 1,
    "title": "Old Milestone",
    "closed_at": "2026-03-01T10:00:00Z",
    "closed_issues": 10,
    "open_issues": 0,
}

_MILESTONE_TOO_FEW_SLICES = {
    "number": 43,
    "title": "Small Milestone",
    "closed_at": "2026-05-19T10:00:00Z",
    "closed_issues": 1,
    "open_issues": 0,
}

_MILESTONE_BORDERLINE = {
    "number": 44,
    "title": "Borderline Milestone",
    "closed_at": "2026-05-19T10:00:00Z",
    "closed_issues": 3,
    "open_issues": 0,
}


def _mock_subprocess(data: list[dict], returncode: int = 0):
    """Create a mocked subprocess.run return."""
    result = MagicMock()
    result.stdout = json.dumps(data)
    result.returncode = returncode
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_qualified_milestone_triggers_sweep():
    """A recently-closed milestone with >=3 closed issues triggers a recommendation."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess([_MILESTONE_WITHIN_WINDOW])
        result = _check_milestone_sweep(repo="Osasuwu/jarvis", days=7, min_slices=3, _now=_NOW)

    assert result is not None
    assert "#42" in result
    assert "Test Milestone" in result
    assert "architecture" in result


def test_old_milestone_no_trigger():
    """A milestone closed outside the window does NOT trigger a recommendation."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess([_MILESTONE_OLD])
        result = _check_milestone_sweep(repo="Osasuwu/jarvis", days=7, min_slices=3, _now=_NOW)

    assert result is None


def test_too_few_slices_no_trigger():
    """A milestone with <3 closed issues does NOT trigger a recommendation."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess([_MILESTONE_TOO_FEW_SLICES])
        result = _check_milestone_sweep(repo="Osasuwu/jarvis", days=7, min_slices=3, _now=_NOW)

    assert result is None


def test_borderline_qualifies():
    """A milestone with exactly min_slices qualifies."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess([_MILESTONE_BORDERLINE])
        result = _check_milestone_sweep(repo="Osasuwu/jarvis", days=7, min_slices=3, _now=_NOW)

    assert result is not None
    assert "#44" in result


def test_mixed_milestones_filters_correctly():
    """Only milestones meeting BOTH criteria (recency + slice count) trigger."""

    all_milestones = [
        _MILESTONE_WITHIN_WINDOW,
        _MILESTONE_OLD,
        _MILESTONE_TOO_FEW_SLICES,
        _MILESTONE_BORDERLINE,
    ]
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess(all_milestones)
        result = _check_milestone_sweep(repo="Osasuwu/jarvis", days=7, min_slices=3, _now=_NOW)

    assert result is not None
    # #42 (5 slices) and #44 (3 slices) are within the 7-day window; #1 is old; #43 too few slices.
    assert "#42" in result
    assert "#44" in result
    assert "#1" not in result
    assert "#43" not in result
    assert "milestone" in result.lower() or "Milestone" in result


def test_gh_not_available_returns_none():
    """When gh CLI is missing, function returns None gracefully."""
    with patch.object(_session_context.subprocess, "run", side_effect=FileNotFoundError):
        result = _check_milestone_sweep(repo="Osasuwu/jarvis")

    assert result is None


def test_gh_api_error_returns_none():
    """When gh API call fails, function returns None gracefully."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess([], returncode=1)
        result = _check_milestone_sweep(repo="Osasuwu/jarvis")

    assert result is None


def test_gh_malformed_json_returns_none():
    """When gh returns malformed JSON, function returns None gracefully."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = "not valid json"
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        result = _check_milestone_sweep(repo="Osasuwu/jarvis")

    assert result is None


def test_empty_milestone_list_returns_none():
    """When no milestones exist, function returns None."""
    with patch.object(_session_context.subprocess, "run") as mock_run:
        mock_run.return_value = _mock_subprocess([])
        result = _check_milestone_sweep(repo="Osasuwu/jarvis", days=7, min_slices=3, _now=_NOW)

    assert result is None
