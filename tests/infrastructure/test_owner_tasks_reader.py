"""Tests for the owner-assigned task_queue reader in scripts/session-context.py
(#1392 AC3): escalations dispatch() writes to task_queue with assignee='owner'
had no reader — SESSIONSTART/PARK_MONDAY escalations were invisible until a
session-start surface exists.

Same importlib-loading scaffolding as test_milestone_sweep.py /
test_session_context_recovery.py (hyphenated filename blocks a normal import).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


for _stub in ("dotenv", "supabase"):
    mod = sys.modules.setdefault(_stub, types.ModuleType(_stub))
    if _stub == "dotenv":
        mod.load_dotenv = lambda *a, **k: None
    if _stub == "supabase" and not hasattr(mod, "create_client"):
        mod.create_client = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PATH = _REPO_ROOT / "scripts" / "session-context.py"
_spec = importlib.util.spec_from_file_location("session_context_owner_tasks", _PATH)
sc = importlib.util.module_from_spec(_spec)
_sys_path_restore = list(sys.path)
sys.path.insert(0, str(_REPO_ROOT))
_spec.loader.exec_module(sc)
sys.path = _sys_path_restore


# ---------------------------------------------------------------------------
# Fake client — same chain-recording shape as test_session_context_recovery.py
# ---------------------------------------------------------------------------
class _FakeTable:
    def __init__(self, rows, call_log=None):
        self._rows = rows
        self._call_log = call_log if call_log is not None else []

    def select(self, *a, **kw):
        self._call_log.append(("select", a, kw))
        return self

    def eq(self, *a, **kw):
        self._call_log.append(("eq", a, kw))
        return self

    def order(self, *a, **kw):
        self._call_log.append(("order", a, kw))
        return self

    def execute(self):
        self._call_log.append(("execute", (), {}))
        return types.SimpleNamespace(data=list(self._rows))


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self.call_log = []

    def table(self, name):
        assert name == "task_queue"
        return _FakeTable(self._rows, self.call_log)


_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "goal": "review escalated PR #999",
    "assignee": "owner",
    "status": "pending",
    "priority": 5,
    "escalated_reason": "review flagged CRITICAL",
    "created_at": "2026-08-08T00:00:00Z",
}


# ---------------------------------------------------------------------------
# _query_owner_tasks
# ---------------------------------------------------------------------------
def test_query_owner_tasks_returns_formatted_section():
    client = _FakeClient([_ROW])
    result = sc._query_owner_tasks(client)
    assert result is not None
    assert "Owner-Assigned Tasks" in result
    assert "review escalated PR #999" in result
    assert "review flagged CRITICAL" in result


def test_query_owner_tasks_no_rows_returns_none():
    client = _FakeClient([])
    assert sc._query_owner_tasks(client) is None


def test_query_owner_tasks_filters_on_assignee_and_status():
    client = _FakeClient([_ROW])
    sc._query_owner_tasks(client)
    calls = client.call_log
    eq_calls = [(a, kw) for (name, a, kw) in calls if name == "eq"]
    assert (("assignee", "owner"), {}) in eq_calls
    assert (("status", "pending"), {}) in eq_calls


def test_query_owner_tasks_handles_query_error(capsys):
    class _BoomClient:
        def table(self, _):
            raise RuntimeError("boom")

    assert sc._query_owner_tasks(_BoomClient()) is None
    err = capsys.readouterr().err
    assert "owner tasks query failed" in err


# ---------------------------------------------------------------------------
# _fmt_owner_task
# ---------------------------------------------------------------------------
def test_fmt_owner_task_includes_goal_and_reason():
    line = sc._fmt_owner_task(_ROW)
    assert "review escalated PR #999" in line
    assert "review flagged CRITICAL" in line


def test_fmt_owner_task_handles_missing_reason():
    row = dict(_ROW)
    row.pop("escalated_reason")
    line = sc._fmt_owner_task(row)
    assert "review escalated PR #999" in line
