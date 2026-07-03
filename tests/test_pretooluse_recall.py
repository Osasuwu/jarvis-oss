"""Unit tests for scripts/pretooluse-recall-hook.py (#332).

Covers:
  - ``_derive_query``: per-tool mapping from tool_input -> recall query.
  - ``is_duplicate`` / ``record_query``: 60s dedup window.
  - ``detect_project``: cwd-basename gating.
  - End-to-end ``main()`` with mocked stdin + supabase client: verifies the
    ``additionalContext`` payload shape and that unmatched tool names exit
    silently.

Loads the hook by path because its filename uses a dash.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Stub optional deps so the module can import without a venv present.
for _stub in ("dotenv", "supabase"):
    if _stub not in sys.modules:
        try:
            __import__(_stub)
        except ImportError:
            mod = types.ModuleType(_stub)
            if _stub == "dotenv":
                mod.load_dotenv = lambda *a, **k: None
            if _stub == "supabase":
                mod.create_client = lambda *a, **k: MagicMock()
            sys.modules[_stub] = mod


_HOOK_PATH = Path(__file__).resolve().parent.parent / "scripts" / "pretooluse-recall-hook.py"
_spec = importlib.util.spec_from_file_location("pretooluse_recall_hook", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


# ---------------------------------------------------------------------------
# _derive_query
# ---------------------------------------------------------------------------


class TestDeriveQuery:
    def test_task_uses_description(self):
        q = hook._derive_query(
            "Task",
            {"description": "branch ship-readiness audit", "subagent_type": "general-purpose"},
        )
        assert q is not None
        assert "delegation" in q
        assert "branch ship-readiness" in q

    def test_task_without_description_skipped(self):
        assert hook._derive_query("Task", {}) is None

    def test_edit_on_markdown_triggers(self):
        q = hook._derive_query("Edit", {"file_path": "C:/repo/jarvis/docs/PROJECT_PLAN.md"})
        assert q is not None
        assert "state in docs" in q
        assert "PROJECT_PLAN" in q

    def test_write_on_python_skipped(self):
        # Only md triggers — non-md file edits bypass the hook so the
        # recall budget isn't spent on every code edit.
        assert hook._derive_query("Write", {"file_path": "scripts/x.py"}) is None

    def test_edit_with_windows_separators(self):
        q = hook._derive_query("Edit", {"file_path": r"C:\\repo\\docs\\STATUS.md"})
        assert q is not None
        assert "STATUS" in q

    def test_memory_store_uses_name_and_type(self):
        q = hook._derive_query(
            "mcp__memory__memory_store",
            {"name": "feedback_symmetric_fixes", "type": "feedback"},
        )
        assert q is not None
        assert "feedback_symmetric_fixes" in q
        assert "feedback" in q

    def test_memory_store_without_name_skipped(self):
        assert hook._derive_query("mcp__memory__memory_store", {"type": "feedback"}) is None

    def test_record_decision_uses_first_sentence(self):
        q = hook._derive_query(
            "mcp__memory__record_decision",
            {
                "decision": (
                    "implement #332 inline. Rationale paragraph follows with "
                    "multiple sentences that are less useful for recall."
                )
            },
        )
        assert q is not None
        # First sentence only — prevents the paragraph-sized rationale from
        # drowning the keyword signal.
        assert "implement #332 inline" in q
        assert "Rationale paragraph" not in q

    def test_bash_gh_issue_create_triggers(self):
        q = hook._derive_query(
            "Bash",
            {"command": "gh issue create --title 'x' --body 'y'"},
        )
        assert q is not None
        assert "issue conventions" in q

    def test_bash_gh_pr_create_triggers(self):
        q = hook._derive_query("Bash", {"command": "gh pr create --title 'x'"})
        assert q is not None
        assert "pr hygiene" in q or "issue conventions" in q

    def test_bash_other_commands_skipped(self):
        # Broader Bash coverage is an explicit non-goal per #332 — too
        # noisy for a mid-turn hook. Only gh create patterns match.
        assert hook._derive_query("Bash", {"command": "ls -la"}) is None
        assert hook._derive_query("Bash", {"command": "gh issue view 123"}) is None
        assert hook._derive_query("Bash", {"command": "pytest tests/ -q"}) is None

    def test_unknown_tool_returns_none(self):
        assert hook._derive_query("Read", {"file_path": "x.py"}) is None
        assert hook._derive_query("Grep", {"pattern": "foo"}) is None

    def test_non_dict_input_returns_none(self):
        assert hook._derive_query("Task", None) is None
        assert hook._derive_query("Task", "description") is None


# ---------------------------------------------------------------------------
# Dedup cache
# ---------------------------------------------------------------------------


class TestDedupCache:
    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path, monkeypatch):
        """Redirect CACHE_FILE into a pytest-managed tmp dir so tests don't
        touch the user's real ~/.claude/cache/."""
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(hook, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(hook, "CACHE_FILE", cache_dir / "pretooluse-recall-dedup.json")
        yield

    def test_fresh_query_not_duplicate(self):
        assert hook.is_duplicate("delegation audit", project="jarvis") is False

    def test_repeat_within_window_is_duplicate(self):
        hook.record_query("delegation audit", project="jarvis", now=1000.0)
        # 30s later — still inside 60s window.
        assert hook.is_duplicate("delegation audit", project="jarvis", now=1030.0) is True

    def test_expired_entry_not_duplicate(self):
        hook.record_query("delegation audit", project="jarvis", now=1000.0)
        # 120s later — window elapsed.
        assert hook.is_duplicate("delegation audit", project="jarvis", now=1120.0) is False

    def test_different_query_not_duplicate(self):
        hook.record_query("delegation audit", project="jarvis", now=1000.0)
        assert hook.is_duplicate("memory dup check", project="jarvis", now=1010.0) is False

    def test_project_scoping_disambiguates(self):
        """Same query in two projects is NOT a duplicate — jarvis and
        redrobot care about different rules."""
        hook.record_query("issue conventions", project="jarvis", now=1000.0)
        assert hook.is_duplicate("issue conventions", project="redrobot", now=1010.0) is False

    def test_missing_cache_file_treated_as_empty(self):
        # No record_query call → cache file absent.
        assert hook.is_duplicate("anything", project="jarvis") is False

    def test_corrupted_cache_file_treated_as_empty(self, tmp_path):
        hook.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        hook.CACHE_FILE.write_text("{not valid json", encoding="utf-8")
        assert hook.is_duplicate("x", project=None) is False

    def test_prune_drops_expired_entries(self):
        hook.record_query("old", project=None, now=1000.0)
        hook.record_query("fresh", project=None, now=1050.0)
        # Next write at t=1100 — "old" (100s stale) should get dropped.
        hook.record_query("new", project=None, now=1100.0)
        cache = hook._load_cache()
        # "old" hash should be gone; "fresh" (50s stale, inside TTL) and
        # "new" (just written) remain.
        old_hash = hook._query_hash("old", project=None)
        fresh_hash = hook._query_hash("fresh", project=None)
        new_hash = hook._query_hash("new", project=None)
        assert old_hash not in cache
        assert fresh_hash in cache
        assert new_hash in cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_first_sentence_splits_on_period(self):
        assert hook._first_sentence("First call. Second sentence.") == "First call."

    def test_first_sentence_caps_max_chars(self):
        long = "x" * 500
        out = hook._first_sentence(long, max_chars=100)
        assert len(out) == 100

    def test_first_sentence_empty_in_empty_out(self):
        assert hook._first_sentence("") == ""

    def test_is_markdown_path(self):
        assert hook._is_markdown_path("foo.md") is True
        assert hook._is_markdown_path("a/b/README.MD") is True
        assert hook._is_markdown_path("foo.markdown") is True
        assert hook._is_markdown_path("foo.py") is False
        assert hook._is_markdown_path("") is False
        assert hook._is_markdown_path("foo.md.bak") is False

    def test_detect_project_known(self):
        assert hook.detect_project("/c/Users/jdoe/GitHub/jarvis") == "jarvis"
        assert hook.detect_project("/c/Users/jdoe/GitHub/redrobot") == "redrobot"

    def test_detect_project_unknown_returns_none(self):
        assert hook.detect_project("/c/Users/jdoe/GitHub/other") is None

    def test_detect_project_none_cwd(self):
        assert hook.detect_project(None) is None


# ---------------------------------------------------------------------------
# End-to-end main()
# ---------------------------------------------------------------------------


class _FakeSupabaseResponse:
    """Fake response returned by FakeSupabaseClient.rpc().execute()."""

    def __init__(self, data: list[dict]) -> None:
        self.data = data


class FakeSupabaseClient:
    """Supabase-shaped test double for keyword_search_memories RPC.

    Replaces the deep ``client.rpc.return_value.execute.return_value.data``
    mock chain with a real method chain that returns controlled response data.
    """

    def __init__(self, *, rpc_rows: list[dict] | None = None) -> None:
        self._rpc_rows = rpc_rows or []
        self.rpc_calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict) -> FakeSupabaseClient:
        self.rpc_calls.append((name, params))
        return self

    def execute(self) -> _FakeSupabaseResponse:
        return _FakeSupabaseResponse(self._rpc_rows)


def _run_main(
    stdin_payload: dict,
    monkeypatch,
    tmp_path,
    *,
    rpc_rows: list[dict] | None = None,
    supabase_url: str | None = "https://example.supabase.co",
    supabase_key: str | None = "test-key",
) -> tuple[int, str]:
    """Invoke hook.main() with mocked stdin + supabase. Returns (exit_code, stdout)."""
    # Isolate cache
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(hook, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(hook, "CACHE_FILE", cache_dir / "pretooluse-recall-dedup.json")

    # Env
    if supabase_url is None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_URL", supabase_url)
    if supabase_key is None:
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_KEY", supabase_key)

    # Mock stdin
    raw = json.dumps(stdin_payload).encode("utf-8")
    fake_stdin = MagicMock()
    fake_stdin.buffer.read.return_value = raw
    monkeypatch.setattr("sys.stdin", fake_stdin)

    # Capture stdout
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    # Mock supabase create_client -> FakeSupabaseClient with real rpc().execute()
    client = FakeSupabaseClient(rpc_rows=rpc_rows)
    fake_supabase = types.SimpleNamespace(create_client=lambda *a, **k: client)
    monkeypatch.setitem(sys.modules, "supabase", fake_supabase)

    exit_code = 0
    try:
        hook.main()
    except SystemExit as e:
        exit_code = int(e.code) if e.code is not None else 0
    return exit_code, buf.getvalue()


class TestMainIntegration:
    def test_unmatched_tool_exits_silent(self, monkeypatch, tmp_path):
        code, out = _run_main(
            {"tool_name": "Read", "tool_input": {"file_path": "x.py"}},
            monkeypatch,
            tmp_path,
        )
        assert code == 0
        assert out == ""

    def test_missing_credentials_exits_silent(self, monkeypatch, tmp_path):
        code, out = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "delegate X to agent"},
            },
            monkeypatch,
            tmp_path,
            supabase_url=None,
        )
        assert code == 0
        assert out == ""

    def test_empty_rpc_result_exits_silent(self, monkeypatch, tmp_path):
        code, out = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "delegate X to agent"},
            },
            monkeypatch,
            tmp_path,
            rpc_rows=[],
        )
        assert code == 0
        assert out == ""

    def test_task_match_emits_additional_context(self, monkeypatch, tmp_path):
        rows = [
            {
                "name": "verify_agent_findings_against_memory",
                "type": "feedback",
                "project": "jarvis",
                "description": "Always cross-check agent output against memory",
                "rank": 0.4,
            },
            {
                "name": "merge_without_confirmation",
                "type": "feedback",
                "project": None,
                "description": "Autonomous merges when green",
                "rank": 0.2,
            },
        ]
        code, out = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "audit ship readiness"},
                "cwd": "C:/Users/jdoe/GitHub/jarvis",
            },
            monkeypatch,
            tmp_path,
            rpc_rows=rows,
        )
        assert code == 0
        payload = json.loads(out)
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        ctx = hso["additionalContext"]
        assert "Mid-turn recall for Task" in ctx
        assert "verify_agent_findings_against_memory" in ctx
        assert "merge_without_confirmation" in ctx

    def test_dedup_skips_repeat_call(self, monkeypatch, tmp_path):
        rows = [
            {
                "name": "x",
                "type": "feedback",
                "project": None,
                "description": "d",
                "rank": 0.5,
            }
        ]
        # First call emits.
        code1, out1 = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "delegate Y to agent"},
                "cwd": "C:/Users/jdoe/GitHub/jarvis",
            },
            monkeypatch,
            tmp_path,
            rpc_rows=rows,
        )
        assert code1 == 0
        assert out1, "first call should emit"
        # Second identical call is a dedup-hit → silent.
        code2, out2 = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "delegate Y to agent"},
                "cwd": "C:/Users/jdoe/GitHub/jarvis",
            },
            monkeypatch,
            tmp_path,
            rpc_rows=rows,
        )
        assert code2 == 0
        assert out2 == "", "second call within TTL must be deduped"

    def test_memory_store_dup_warning_in_context(self, monkeypatch, tmp_path):
        """Smoke: about-to-be-dup memory_store surfaces the existing
        memory in additionalContext (the agent sees the near-dup before
        the store fires)."""
        rows = [
            {
                "name": "feedback_symmetric_fixes",
                "type": "feedback",
                "project": "jarvis",
                "description": "When fixing a class of bug, grep for sibling instances",
                "rank": 0.7,
            }
        ]
        code, out = _run_main(
            {
                "tool_name": "mcp__memory__memory_store",
                "tool_input": {
                    "name": "feedback_symmetric_fixes_revisit",
                    "type": "feedback",
                    "content": "...",
                },
                "cwd": "C:/Users/jdoe/GitHub/jarvis",
            },
            monkeypatch,
            tmp_path,
            rpc_rows=rows,
        )
        assert code == 0
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "feedback_symmetric_fixes" in ctx

    def test_filters_below_min_match_score(self, monkeypatch, tmp_path):
        rows = [
            {
                "name": "noise",
                "type": "feedback",
                "project": None,
                "description": "d",
                "rank": 0.01,  # below MIN_MATCH_SCORE
            }
        ]
        code, out = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "delegate Z"},
            },
            monkeypatch,
            tmp_path,
            rpc_rows=rows,
        )
        assert code == 0
        # All rows filtered out → silent exit.
        assert out == ""

    def test_excludes_disallowed_types(self, monkeypatch, tmp_path):
        rows = [
            {
                "name": "some_user_memory",
                "type": "user",  # loaded at session start, excluded here
                "project": "jarvis",
                "description": "user role note",
                "rank": 0.5,
            }
        ]
        code, out = _run_main(
            {
                "tool_name": "Task",
                "tool_input": {"description": "delegate deep"},
            },
            monkeypatch,
            tmp_path,
            rpc_rows=rows,
        )
        assert code == 0
        assert out == ""
