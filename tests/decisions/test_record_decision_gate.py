"""Tests for scripts/record-decision-gate.py — Tier 2 hook (#524, #1269, #1421)."""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

from lib import recall_dedup as rd

# Stub optional deps so the module can import without a venv present —
# mirrors tests/memory/test_pretooluse_recall.py, whose record_decision
# recall logic this gate absorbed in #1421.
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
gate = importlib.import_module("record-decision-gate")

_HOOK = Path(__file__).resolve().parent.parent.parent / "scripts" / "record-decision-gate.py"


# ── Pure evaluator ───────────────────────────────────────────────────


def test_blocks_when_memories_used_missing():
    assert gate.evaluate("mcp__memory__record_decision", {}) is True


def test_blocks_when_memories_used_empty_list():
    assert gate.evaluate("mcp__memory__record_decision", {"memories_used": []}) is True


def test_blocks_when_memories_used_none():
    assert gate.evaluate("mcp__memory__record_decision", {"memories_used": None}) is True


def test_passes_when_memories_used_has_uuids():
    uuids = ["11111111-1111-1111-1111-111111111111"]
    assert gate.evaluate("mcp__memory__record_decision", {"memories_used": uuids}) is False


def test_passes_when_intentionally_empty_true_and_empty_list():
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"memories_used": [], "intentionally_empty": True},
        )
        is False
    )


def test_passes_when_intentionally_empty_true_and_missing():
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"intentionally_empty": True},
        )
        is False
    )


def test_intentionally_empty_false_does_not_bypass():
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"memories_used": [], "intentionally_empty": False},
        )
        is True
    )


def test_other_tool_never_blocks():
    assert gate.evaluate("Bash", {"command": "ls"}) is False
    assert gate.evaluate("mcp__memory__memory_store", {}) is False


def test_intentionally_empty_overrides_even_with_full_list():
    # Flag is checked first; non-empty list also passes — both routes work.
    uuids = ["11111111-1111-1111-1111-111111111111"]
    assert (
        gate.evaluate(
            "mcp__memory__record_decision",
            {"memories_used": uuids, "intentionally_empty": True},
        )
        is False
    )


# ── Session-id sanitizer (#1269) ─────────────────────────────────────


def test_sanitize_accepts_harness_uuid():
    sid = "fe22ddae-340c-4c5b-b8d7-82a4df8396ee"
    assert gate.sanitize_session_id(sid) == sid


def test_sanitize_accepts_underscore_and_dash():
    assert gate.sanitize_session_id("abc_DEF-123") == "abc_DEF-123"


def test_sanitize_rejects_bad_chars():
    assert gate.sanitize_session_id("abc def") is None
    assert gate.sanitize_session_id("abc/../etc") is None
    assert gate.sanitize_session_id("") is None


def test_sanitize_rejects_overlong():
    assert gate.sanitize_session_id("a" * 129) is None
    assert gate.sanitize_session_id("a" * 128) == "a" * 128


def test_sanitize_rejects_non_string():
    assert gate.sanitize_session_id(None) is None
    assert gate.sanitize_session_id(42) is None


# ── End-to-end: subprocess with stdin JSON ───────────────────────────


def _run_hook(input_obj: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(input_obj),
        text=True,
        capture_output=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout


def test_subprocess_blocks_empty_with_deny_json():
    rc, out = _run_hook(
        {
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": []},
        }
    )
    assert rc == 2
    payload = json.loads(out)
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    assert inner["permissionDecision"] == "deny"
    assert "memories_used" in inner["permissionDecisionReason"]
    assert "CLAUDE.md" in inner["permissionDecisionReason"]
    assert "intentionally_empty" in inner["permissionDecisionReason"]


def test_subprocess_passes_with_uuids():
    # No deny — allow path now always stamps cwd (#1423), so this is no
    # longer a fully-silent exit; assert the gate passed, not the wire shape.
    rc, out = _run_hook(
        {
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": ["11111111-1111-1111-1111-111111111111"]},
        }
    )
    assert rc == 0
    inner = json.loads(out)["hookSpecificOutput"]
    assert "permissionDecision" not in inner
    assert inner["updatedInput"]["memories_used"] == ["11111111-1111-1111-1111-111111111111"]


def test_subprocess_passes_with_intentionally_empty():
    rc, out = _run_hook(
        {
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [], "intentionally_empty": True},
        }
    )
    assert rc == 0
    inner = json.loads(out)["hookSpecificOutput"]
    assert "permissionDecision" not in inner
    assert inner["updatedInput"]["intentionally_empty"] is True


def test_subprocess_silent_on_other_tool():
    rc, out = _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert rc == 0
    assert out == ""


def test_subprocess_silent_on_malformed_stdin():
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="not json at all",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_subprocess_silent_on_empty_stdin():
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0


# ── Session-id stamping via updatedInput (#1269) ─────────────────────


_SID = "fe22ddae-340c-4c5b-b8d7-82a4df8396ee"
_UUID = "11111111-1111-1111-1111-111111111111"


def test_subprocess_injects_session_id_on_allow():
    rc, out = _run_hook(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [_UUID]},
        }
    )
    assert rc == 0
    payload = json.loads(out)
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    # updatedInput without permissionDecision: transparent input mutation
    # that neither bypasses permission dialogs nor races the recall
    # context this same process may also emit (#1421 — one process now).
    assert "permissionDecision" not in inner
    updated = inner["updatedInput"]
    assert updated["memories_used"] == [_UUID]
    assert updated["session_id"] == _SID


def test_subprocess_injects_on_intentionally_empty_allow():
    rc, out = _run_hook(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [], "intentionally_empty": True},
        }
    )
    assert rc == 0
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert updated["session_id"] == _SID
    assert updated["intentionally_empty"] is True


def test_subprocess_harness_sid_overrides_model_supplied():
    # The harness stdin sid is ground truth; a model-hallucinated value in
    # tool_input must not survive.
    rc, out = _run_hook(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [_UUID], "session_id": "made-up"},
        }
    )
    assert rc == 0
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert updated["session_id"] == _SID


def test_subprocess_stamps_cwd_when_sid_missing():
    # #1423: cwd is stamped independently of session_id validity — cwd has
    # no sanitization failure mode (falls back to os.getcwd()), so a missing
    # sid no longer means a fully silent exit.
    rc, out = _run_hook(
        {
            "cwd": "/some/project/path",
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [_UUID]},
        }
    )
    assert rc == 0
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert updated["cwd"] == "/some/project/path"
    assert "session_id" not in updated


def test_subprocess_stamps_cwd_when_sid_malformed():
    rc, out = _run_hook(
        {
            "session_id": "not a valid sid!",
            "cwd": "/some/project/path",
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [_UUID]},
        }
    )
    assert rc == 0
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert updated["cwd"] == "/some/project/path"
    assert "session_id" not in updated


def test_subprocess_injects_cwd_alongside_session_id():
    # #1423 AC: cwd stamped alongside session_id — cwd becomes the recovery
    # key (project, cwd, window); session_id stays as forensic metadata.
    rc, out = _run_hook(
        {
            "session_id": _SID,
            "cwd": "/some/project/path",
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [_UUID]},
        }
    )
    assert rc == 0
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert updated["cwd"] == "/some/project/path"
    assert updated["session_id"] == _SID


def test_subprocess_no_injection_for_other_tools():
    rc, out = _run_hook(
        {
            "session_id": _SID,
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    assert rc == 0
    assert out == ""


def test_subprocess_deny_still_wins_with_sid_present():
    rc, out = _run_hook(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": []},
        }
    )
    assert rc == 2
    inner = json.loads(out)["hookSpecificOutput"]
    assert inner["permissionDecision"] == "deny"
    assert "updatedInput" not in inner


# ── Mid-turn recall (#332), consolidated in-process (#1421) ─────────


class _FakeSupabaseResponse:
    def __init__(self, data):
        self.data = data


class FakeSupabaseClient:
    def __init__(self, *, rpc_rows=None):
        self._rpc_rows = rpc_rows or []
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return self

    def execute(self):
        return _FakeSupabaseResponse(self._rpc_rows)


def _run_gate_main(
    input_obj: dict,
    monkeypatch,
    tmp_path: Path,
    *,
    rpc_rows: list[dict] | None = None,
    supabase_url: str | None = "https://example.supabase.co",
    supabase_key: str | None = "test-key",
) -> tuple[int, str]:
    """In-process gate.main() run with mocked cache/env/stdin/stdout/supabase.

    Mirrors _run_main in tests/memory/test_pretooluse_recall.py, adapted to
    the gate module's attribute names.
    """
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(gate, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(gate, "CACHE_FILE", cache_dir / "pretooluse-recall-dedup.json")
    monkeypatch.setattr(gate, "STATS_FILE", cache_dir / "pretooluse-recall-stats.json")

    compaction_dir = tmp_path / "compaction"
    dedup_dir = tmp_path / "dedup"
    monkeypatch.setattr(rd, "COMPACTION_DIR", compaction_dir)
    monkeypatch.setattr(rd, "DEDUP_DIR", dedup_dir)

    if supabase_url is None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_URL", supabase_url)
    if supabase_key is None:
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_KEY", supabase_key)

    fake_client = FakeSupabaseClient(rpc_rows=rpc_rows)
    fake_module = types.ModuleType("supabase")
    fake_module.create_client = lambda *a, **k: fake_client
    monkeypatch.setitem(sys.modules, "supabase", fake_module)

    stdin = io.StringIO(json.dumps(input_obj))
    stdin.buffer = io.BytesIO(json.dumps(input_obj).encode("utf-8"))
    monkeypatch.setattr(sys, "stdin", stdin)

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    rc = 0
    try:
        gate.main()
    except SystemExit as exc:
        rc = exc.code or 0

    return rc, stdout.getvalue()


def test_recall_context_accompanies_session_id_stamp(monkeypatch, tmp_path):
    # AC bullet #3: a valid session_id AND a decision text that yields RPC
    # hits both survive together in the SAME hookSpecificOutput — the
    # direct regression test for the race #1421 closes.
    rows = [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "some_memory",
            "type": "decision",
            "project": "jarvis",
            "description": "Relevant prior decision.",
            "rank": 0.5,
        }
    ]
    rc, out = _run_gate_main(
        {
            "session_id": _SID,
            "cwd": str(tmp_path / "jarvis"),
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {
                "memories_used": [_UUID],
                "decision": "Consolidate the two record_decision hooks into one script.",
            },
        },
        monkeypatch,
        tmp_path,
        rpc_rows=rows,
    )
    assert rc == 0
    inner = json.loads(out)["hookSpecificOutput"]
    assert inner["updatedInput"]["session_id"] == _SID
    assert inner["updatedInput"]["memories_used"] == [_UUID]
    assert "additionalContext" in inner
    assert "some_memory" in inner["additionalContext"]


def test_recall_silent_when_credentials_missing(monkeypatch, tmp_path):
    rc, out = _run_gate_main(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {
                "memories_used": [_UUID],
                "decision": "Consolidate the two record_decision hooks into one script.",
            },
        },
        monkeypatch,
        tmp_path,
        supabase_url=None,
        supabase_key=None,
    )
    assert rc == 0
    inner = json.loads(out)["hookSpecificOutput"]
    assert inner["updatedInput"]["session_id"] == _SID
    assert "additionalContext" not in inner


def test_recall_silent_when_rpc_empty(monkeypatch, tmp_path):
    rc, out = _run_gate_main(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {
                "memories_used": [_UUID],
                "decision": "Consolidate the two record_decision hooks into one script.",
            },
        },
        monkeypatch,
        tmp_path,
        rpc_rows=[],
    )
    assert rc == 0
    inner = json.loads(out)["hookSpecificOutput"]
    assert inner["updatedInput"]["session_id"] == _SID
    assert "additionalContext" not in inner


def test_recall_silent_when_no_decision_text(monkeypatch, tmp_path):
    rows = [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "some_memory",
            "type": "decision",
            "project": "jarvis",
            "description": "Relevant prior decision.",
            "rank": 0.5,
        }
    ]
    rc, out = _run_gate_main(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {"memories_used": [_UUID]},
        },
        monkeypatch,
        tmp_path,
        rpc_rows=rows,
    )
    assert rc == 0
    inner = json.loads(out)["hookSpecificOutput"]
    assert inner["updatedInput"]["session_id"] == _SID
    assert "additionalContext" not in inner


def test_recall_dedup_skips_repeat_query(monkeypatch, tmp_path):
    rows = [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "some_memory",
            "type": "decision",
            "project": "jarvis",
            "description": "Relevant prior decision.",
            "rank": 0.5,
        }
    ]
    payload = {
        "session_id": _SID,
        "tool_name": "mcp__memory__record_decision",
        "tool_input": {
            "memories_used": [_UUID],
            "decision": "Consolidate the two record_decision hooks into one script.",
        },
    }
    rc1, out1 = _run_gate_main(payload, monkeypatch, tmp_path, rpc_rows=rows)
    assert rc1 == 0
    assert "additionalContext" in json.loads(out1)["hookSpecificOutput"]

    rc2, out2 = _run_gate_main(payload, monkeypatch, tmp_path, rpc_rows=rows)
    assert rc2 == 0
    inner2 = json.loads(out2)["hookSpecificOutput"]
    assert inner2["updatedInput"]["session_id"] == _SID
    assert "additionalContext" not in inner2


def test_recall_never_runs_on_deny(monkeypatch, tmp_path):
    # Deliberate tightening vs. the old two-process world (#1421): recall
    # never fires for a blocked call, even if the decision text would
    # otherwise yield hits.
    rows = [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "some_memory",
            "type": "decision",
            "project": "jarvis",
            "description": "Relevant prior decision.",
            "rank": 0.5,
        }
    ]
    rc, out = _run_gate_main(
        {
            "session_id": _SID,
            "tool_name": "mcp__memory__record_decision",
            "tool_input": {
                "memories_used": [],
                "decision": "Consolidate the two record_decision hooks into one script.",
            },
        },
        monkeypatch,
        tmp_path,
        rpc_rows=rows,
    )
    assert rc == 2
    inner = json.loads(out)["hookSpecificOutput"]
    assert inner["permissionDecision"] == "deny"
    assert "updatedInput" not in inner
    assert "additionalContext" not in inner
