"""Unit tests for scripts/memory-dedup-check.py.

The dedup hook blocks a memory_store when a same-type/same-project memory with a
DIFFERENT name sits above the cosine threshold. That gate must NOT fire on
deliberately-serialized snapshots (the status-record skill writes one row per
UTC date, each ~0.98 similar to the prior day's but with a unique date-keyed
name). These carry `status-snapshot` / `auto-generated` tags and are exempted.

Regression for: status-record cron blocked every day after the first because the
cross-name dup gate treated yesterday's snapshot as a duplicate of today's.

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

# Stub optional deps so the module imports without a venv present.
for _stub in ("dotenv", "supabase", "httpx"):
    if _stub not in sys.modules:
        try:
            __import__(_stub)
        except ImportError:
            mod = types.ModuleType(_stub)
            if _stub == "dotenv":
                mod.load_dotenv = lambda *a, **k: None
            if _stub == "supabase":
                mod.create_client = lambda *a, **k: MagicMock()
            if _stub == "httpx":
                mod.Client = MagicMock()
            sys.modules[_stub] = mod


_HOOK_PATH = Path(__file__).resolve().parent.parent / "scripts" / "memory-dedup-check.py"
_spec = importlib.util.spec_from_file_location("memory_dedup_check", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


# ---------------------------------------------------------------------------
# is_exempt_series — pure predicate
# ---------------------------------------------------------------------------


class TestIsExemptSeries:
    def test_status_snapshot_tag_exempt(self):
        assert hook.is_exempt_series(["status-snapshot", "auto-generated"]) is True

    def test_auto_generated_alone_exempt(self):
        assert hook.is_exempt_series(["auto-generated"]) is True

    def test_ordinary_tags_not_exempt(self):
        assert hook.is_exempt_series(["decision", "architecture"]) is False

    def test_empty_not_exempt(self):
        assert hook.is_exempt_series([]) is False

    def test_none_not_exempt(self):
        assert hook.is_exempt_series(None) is False

    def test_non_list_not_exempt(self):
        # Defensive: a malformed tags value must not crash or falsely exempt.
        assert hook.is_exempt_series("status-snapshot") is False


# ---------------------------------------------------------------------------
# main() — exempt path short-circuits before any network/embedding
# ---------------------------------------------------------------------------


def _run_main(stdin_payload, monkeypatch):
    raw = json.dumps(stdin_payload).encode("utf-8")
    fake_stdin = MagicMock()
    fake_stdin.buffer.read.return_value = raw
    monkeypatch.setattr("sys.stdin", fake_stdin)

    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    # Force-fail embedding so that, IF the exempt short-circuit ever regresses,
    # the test still wouldn't reach a real network call — but a block() would
    # require embedding to succeed, so a regression surfaces as exit!=0 below.
    monkeypatch.setattr(hook, "embed", lambda *a, **k: pytest.fail("embed called for exempt series"))

    exit_code = 0
    try:
        hook.main()
    except SystemExit as e:
        exit_code = int(e.code) if e.code is not None else 0
    return exit_code, buf.getvalue()


class TestMainExemption:
    def test_status_snapshot_store_passes_through(self, monkeypatch):
        code, out = _run_main(
            {
                "tool_name": "mcp__memory__memory_store",
                "tool_input": {
                    "name": "status_snapshot_2026-06-05",
                    "type": "reference",
                    "project": "jarvis",
                    "tags": ["status-snapshot", "auto-generated"],
                    "description": "Status snapshot 2026-06-05",
                    "content": "```yaml\nschema_version: 1\n```",
                },
            },
            monkeypatch,
        )
        # allow() -> exit 0, no deny JSON emitted.
        assert code == 0
        assert out == ""
