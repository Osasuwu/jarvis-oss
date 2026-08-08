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


_HOOK_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "memory-dedup-check.py"
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


def _run_main(stdin_payload, monkeypatch, embed_fn=None):
    raw = json.dumps(stdin_payload).encode("utf-8")
    fake_stdin = MagicMock()
    fake_stdin.buffer.read.return_value = raw
    monkeypatch.setattr("sys.stdin", fake_stdin)

    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    # Force-fail embedding so that, IF the exempt short-circuit ever regresses,
    # the test still wouldn't reach a real network call — but a block() would
    # require embedding to succeed, so a regression surfaces as exit!=0 below.
    # Callers that need dedup to actually run past the embed step pass embed_fn.
    monkeypatch.setattr(
        hook, "embed", embed_fn or (lambda *a, **k: pytest.fail("embed called for exempt series"))
    )

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


# ---------------------------------------------------------------------------
# main() — #1184 regressions: upsert short-circuit + session-snapshot exclusion
# ---------------------------------------------------------------------------


def _fake_client(*, existing_row: bool, rpc_rows: list[dict]):
    """Build a MagicMock supabase client for the two query chains main() uses:

    - client.table("memories").select("id").eq("name",...).is_("deleted_at","null")
      .eq("project",...).limit(1).execute()   (row_exists check, project truthy branch)
    - client.rpc("match_memories", {...}).execute()
    """
    client = MagicMock()

    row_exists_chain = (
        client.table.return_value.select.return_value.eq.return_value.is_.return_value
    )
    row_exists_chain.eq.return_value.limit.return_value.execute.return_value.data = (
        [{"id": "existing-id"}] if existing_row else []
    )

    client.rpc.return_value.execute.return_value.data = rpc_rows
    return client


class TestMainUpsertShortCircuit:
    def test_existing_project_name_row_skips_dedup_entirely(self, monkeypatch):
        """AC1: memory_store against an existing (project, name) upserts without
        consulting the dedup guard — embed() must never be called."""
        fake_client = _fake_client(existing_row=True, rpc_rows=[])
        monkeypatch.setattr(hook, "create_client", lambda *a, **k: fake_client)
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")

        code, out = _run_main(
            {
                "tool_name": "mcp__memory__memory_store",
                "tool_input": {
                    "name": "working_state_jarvis",
                    "type": "project",
                    "project": "jarvis",
                    "description": "Working state checkpoint",
                    "content": "some working state content",
                },
            },
            monkeypatch,
        )
        assert code == 0
        assert out == ""


class TestMainSessionSnapshotExclusion:
    def test_working_state_store_survives_similar_session_snapshot(self, monkeypatch):
        """AC2/AC3: a highly-similar session_snapshot_<id> row must not block an
        unrelated working_state_<project> store — it's excluded from the dedup
        candidate set the same way it's excluded from memory_recall (#417)."""
        fake_client = _fake_client(
            existing_row=False,
            rpc_rows=[
                {
                    "name": "session_snapshot_6cda4c6f-0000-0000-0000-000000000000",
                    "project": "jarvis",
                    "type": "project",
                    "description": "Session snapshot",
                    "similarity": 0.79,
                    "tags": ["session-snapshot"],
                }
            ],
        )
        monkeypatch.setattr(hook, "create_client", lambda *a, **k: fake_client)
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")

        code, out = _run_main(
            {
                "tool_name": "mcp__memory__memory_store",
                "tool_input": {
                    "name": "working_state_jarvis",
                    "type": "project",
                    "project": "jarvis",
                    "description": "Working state checkpoint",
                    "content": "some working state content",
                },
            },
            monkeypatch,
            embed_fn=lambda *a, **k: [0.1, 0.2, 0.3],
        )
        # allow() -> exit 0, no deny JSON — the snapshot candidate was filtered
        # out before the same-name check, so no candidates remain to block on.
        assert code == 0
        assert out == ""
