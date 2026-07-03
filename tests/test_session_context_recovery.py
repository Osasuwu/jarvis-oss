"""Unit tests for scripts/session-context.py pre-compact recovery (Phase 2, #279).

Covers the deterministic parts that can be exercised without a live Supabase:
  - `_read_hook_input` — stdin parsing (TTY skip, empty, invalid JSON, valid)
  - `_is_compact_resume` — matcher detection across field-name synonyms
  - `_load_snapshot_from_local` — local fallback + freshness
  - `_format_recovery_section` — stable output shape
  - `_load_snapshot_from_supabase` — freshness logic with a fake client

Network-touching paths (live Supabase fetch) and end-to-end stdout rendering
are verified manually via `/compact` on the live session; the design doc
documents the acceptance flow.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Stub optional deps so module import succeeds on minimal CI
#
# Must handle the case where a local supabase/ directory (the repo's
# migration folder) creates a namespace package, bypassing the ImportError
# fallback. We always override with a proper stub.
# ---------------------------------------------------------------------------
for _stub in ("dotenv", "supabase"):
    try:
        __import__(_stub)
    except ImportError:
        pass
    # Check whether the module is a real installed package or a namespace
    # package / missing — in both cases we need a stub with the expected API.
    mod = sys.modules.get(_stub)
    if mod is None or getattr(mod, "__path__", None) is not None and not hasattr(mod, "__file__") or _stub == "supabase" and not hasattr(mod, "create_client"):
        mod = types.ModuleType(_stub)
        if _stub == "dotenv":
            mod.load_dotenv = lambda *a, **k: None
        if _stub == "supabase":
            mod.create_client = lambda *a, **k: None
        sys.modules[_stub] = mod


_PATH = Path(__file__).resolve().parent.parent / "scripts" / "session-context.py"
_spec = importlib.util.spec_from_file_location("session_context", _PATH)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


# ---------------------------------------------------------------------------
# stdin helpers
# ---------------------------------------------------------------------------
class _FakeStdin(io.StringIO):
    """StringIO that lies about being a TTY — mimics a piped hook input."""

    def __init__(self, payload: str = "", isatty: bool = False):
        super().__init__(payload)
        self._isatty = isatty

    def isatty(self) -> bool:  # noqa: D401 — simple override
        return self._isatty


def _with_stdin(monkeypatch, payload: str, isatty: bool = False):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload, isatty=isatty))


# ---------------------------------------------------------------------------
# _read_hook_input
# ---------------------------------------------------------------------------
def test_read_hook_input_tty_returns_empty(monkeypatch):
    _with_stdin(monkeypatch, "{\"session_id\": \"x\"}", isatty=True)
    assert sc._read_hook_input() == {}


def test_read_hook_input_empty_stdin(monkeypatch):
    _with_stdin(monkeypatch, "", isatty=False)
    assert sc._read_hook_input() == {}


def test_read_hook_input_whitespace_only(monkeypatch):
    _with_stdin(monkeypatch, "   \n  ", isatty=False)
    assert sc._read_hook_input() == {}


def test_read_hook_input_invalid_json(monkeypatch):
    _with_stdin(monkeypatch, "not json", isatty=False)
    assert sc._read_hook_input() == {}


def test_read_hook_input_non_object_json(monkeypatch):
    # A JSON list is syntactically valid but our contract expects an object.
    _with_stdin(monkeypatch, "[1, 2, 3]", isatty=False)
    assert sc._read_hook_input() == {}


def test_read_hook_input_valid_object(monkeypatch):
    _with_stdin(
        monkeypatch,
        '{"session_id": "abc", "hook_event_name": "SessionStart", "source": "compact"}',
        isatty=False,
    )
    data = sc._read_hook_input()
    assert data["session_id"] == "abc"
    assert data["hook_event_name"] == "SessionStart"
    assert data["source"] == "compact"


# ---------------------------------------------------------------------------
# _is_compact_resume
# ---------------------------------------------------------------------------
def test_is_compact_resume_true_for_source_compact():
    hook = {"hook_event_name": "SessionStart", "source": "compact"}
    assert sc._is_compact_resume(hook) is True


def test_is_compact_resume_true_for_matcher_compact():
    hook = {"hook_event_name": "SessionStart", "matcher": "compact"}
    assert sc._is_compact_resume(hook) is True


def test_is_compact_resume_case_insensitive():
    hook = {"hook_event_name": "SessionStart", "source": "Compact"}
    assert sc._is_compact_resume(hook) is True


def test_is_compact_resume_false_for_startup():
    hook = {"hook_event_name": "SessionStart", "source": "startup"}
    assert sc._is_compact_resume(hook) is False


def test_is_compact_resume_false_for_empty_hook():
    # Standalone run (no hook input) must not trigger recovery.
    assert sc._is_compact_resume({}) is False


def test_is_compact_resume_false_for_wrong_event():
    hook = {"hook_event_name": "PreToolUse", "matcher": "compact"}
    assert sc._is_compact_resume(hook) is False


def test_is_compact_resume_allows_missing_event_name():
    # Some invocations might omit `hook_event_name` — don't reject if the
    # matcher still clearly says compact.
    hook = {"source": "compact"}
    assert sc._is_compact_resume(hook) is True


# ---------------------------------------------------------------------------
# _load_snapshot_from_local
# ---------------------------------------------------------------------------
def _snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".claude" / "session-snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_load_snapshot_from_local_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_root", tmp_path)
    assert sc._load_snapshot_from_local("missing-session") is None


def test_load_snapshot_from_local_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_root", tmp_path)
    d = _snapshot_dir(tmp_path)
    f = d / "sid.md"
    f.write_text("# fresh snapshot", encoding="utf-8")
    assert sc._load_snapshot_from_local("sid") == "# fresh snapshot"


def test_load_snapshot_from_local_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_root", tmp_path)
    d = _snapshot_dir(tmp_path)
    f = d / "sid.md"
    f.write_text("# stale", encoding="utf-8")
    # Age it well past the freshness window.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    import os as _os
    _os.utime(f, (old_ts, old_ts))
    assert sc._load_snapshot_from_local("sid") is None


def test_load_snapshot_from_local_empty_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_root", tmp_path)
    d = _snapshot_dir(tmp_path)
    f = d / "sid.md"
    f.write_text("", encoding="utf-8")
    # Empty content should not yield a recovery section.
    assert sc._load_snapshot_from_local("sid") is None


# ---------------------------------------------------------------------------
# _load_snapshot_from_supabase
# ---------------------------------------------------------------------------
class _FakeTable:
    def __init__(self, rows, call_log=None):
        self._rows = rows
        self._call_log = call_log if call_log is not None else []

    # Chain methods — just record calls, return self.
    def select(self, *a, **kw):
        self._call_log.append(("select", a, kw))
        return self

    def eq(self, *a, **kw):
        self._call_log.append(("eq", a, kw))
        return self

    def is_(self, *a, **kw):
        self._call_log.append(("is_", a, kw))
        return self

    def order(self, *a, **kw):
        self._call_log.append(("order", a, kw))
        return self

    def limit(self, *a, **kw):
        self._call_log.append(("limit", a, kw))
        return self

    def execute(self):
        self._call_log.append(("execute", (), {}))
        result = types.SimpleNamespace(data=list(self._rows))
        return result


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self.call_log = []

    def table(self, _name):
        return _FakeTable(self._rows, self.call_log)


def test_load_snapshot_from_supabase_fresh_row():
    now = datetime.now(timezone.utc).isoformat()
    client = _FakeClient([{"content": "# snap", "updated_at": now}])
    assert sc._load_snapshot_from_supabase(client, "sid") == "# snap"


def test_load_snapshot_from_supabase_stale_row():
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    client = _FakeClient([{"content": "# old", "updated_at": stale}])
    assert sc._load_snapshot_from_supabase(client, "sid") is None


def test_load_snapshot_from_supabase_no_rows():
    client = _FakeClient([])
    assert sc._load_snapshot_from_supabase(client, "sid") is None


def test_load_snapshot_from_supabase_handles_query_error(capsys):
    class _BoomClient:
        def table(self, _):
            raise RuntimeError("boom")

    # Must not raise — the session start hook is never blocked by this failure.
    assert sc._load_snapshot_from_supabase(_BoomClient(), "sid") is None
    err = capsys.readouterr().err
    assert "snapshot query failed" in err


def test_load_snapshot_from_supabase_no_updated_at_is_kept():
    # If the row has no updated_at, freshness can't be verified — we
    # optimistically include it. This matches how we wrote the snapshot
    # (upsert always sets updated_at), but guards against schema drift.
    client = _FakeClient([{"content": "# no ts"}])
    assert sc._load_snapshot_from_supabase(client, "sid") == "# no ts"


def test_load_snapshot_from_supabase_with_project_filters_and_orders():
    # Copilot review fix: with project known, ensure we apply eq(project=...)
    # and order by updated_at desc before limit.
    now = datetime.now(timezone.utc).isoformat()
    client = _FakeClient([{"content": "# snap", "updated_at": now}])
    assert sc._load_snapshot_from_supabase(client, "sid", "jarvis") == "# snap"

    ops = client.call_log
    # name filter is there …
    assert ("eq", ("name", "session_snapshot_sid"), {}) in ops
    # deleted_at is_null is there …
    assert ("is_", ("deleted_at", "null"), {}) in ops
    # project filter is applied when supplied …
    assert ("eq", ("project", "jarvis"), {}) in ops
    # order is applied before limit
    order_idx = next(i for i, op in enumerate(ops) if op[0] == "order")
    limit_idx = next(i for i, op in enumerate(ops) if op[0] == "limit")
    assert order_idx < limit_idx
    order_op = ops[order_idx]
    assert order_op[1][0] == "updated_at"
    assert order_op[2].get("desc") is True


def test_load_snapshot_from_supabase_without_project_skips_project_filter():
    now = datetime.now(timezone.utc).isoformat()
    client = _FakeClient([{"content": "# snap", "updated_at": now}])
    assert sc._load_snapshot_from_supabase(client, "sid") == "# snap"

    ops = client.call_log
    # Only the name eq, no project eq.
    eq_calls = [op for op in ops if op[0] == "eq"]
    assert ("eq", ("name", "session_snapshot_sid"), {}) in eq_calls
    assert all(op[1][:1] != ("project",) for op in eq_calls)


def test_load_snapshot_from_supabase_rejects_bad_session_id():
    # Copilot review fix: session_id goes into the Supabase `name` — sanitize
    # before use so a hostile value can't widen the query.
    client = _FakeClient([{"content": "anything", "updated_at": datetime.now(timezone.utc).isoformat()}])
    assert sc._load_snapshot_from_supabase(client, "../escape") is None
    # Supabase must not be queried at all for a rejected id.
    assert client.call_log == []


# ---------------------------------------------------------------------------
# _safe_session_id — path-traversal / injection guard
# ---------------------------------------------------------------------------
def test_safe_session_id_accepts_typical_uuid():
    assert sc._safe_session_id("d47e5fb3-4609-4af1-a271-88a29004c7b3") == "d47e5fb3-4609-4af1-a271-88a29004c7b3"


def test_safe_session_id_accepts_alnum_and_underscore():
    assert sc._safe_session_id("abc_123-XYZ") == "abc_123-XYZ"


def test_safe_session_id_trims_surrounding_whitespace():
    assert sc._safe_session_id("  sid  ") == "sid"


def test_safe_session_id_rejects_path_traversal():
    assert sc._safe_session_id("../../etc/passwd") is None


def test_safe_session_id_rejects_path_separators():
    assert sc._safe_session_id("a/b") is None
    assert sc._safe_session_id("a\\b") is None


def test_safe_session_id_rejects_empty_and_non_str():
    assert sc._safe_session_id("") is None
    assert sc._safe_session_id("   ") is None
    assert sc._safe_session_id(None) is None
    assert sc._safe_session_id(123) is None


def test_safe_session_id_rejects_overlong():
    # Over 128 chars — keep the allowlist tight.
    assert sc._safe_session_id("a" * 129) is None


def test_safe_session_id_rejects_special_chars():
    assert sc._safe_session_id("a b") is None
    assert sc._safe_session_id("a;b") is None
    assert sc._safe_session_id("a$b") is None


# Path-traversal must also be rejected by the local loader, not only the
# sanitizer — this is the end-to-end guarantee.
def test_load_snapshot_from_local_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_root", tmp_path)
    # Seed a file *outside* the snapshots directory that an attacker might
    # try to reach with `../` — confirm we refuse to read it.
    outside = tmp_path / "secrets.md"
    outside.write_text("# sensitive", encoding="utf-8")
    assert sc._load_snapshot_from_local("../secrets") is None


# ---------------------------------------------------------------------------
# _format_recovery_section
# ---------------------------------------------------------------------------
def test_format_recovery_section_has_header_and_body():
    out = sc._format_recovery_section("# body")
    assert out.startswith("## Pre-Compact Recovery\n")
    assert "# body" in out
    assert "pre-compact snapshot" in out.lower()


# ---------------------------------------------------------------------------
# _load_project_context — domain-context auto-load (PR #489)
# ---------------------------------------------------------------------------
def test_load_project_context_returns_none_when_missing(tmp_path):
    """No CONTEXT.md at project_root → silently skip."""
    assert sc._load_project_context(tmp_path) is None


def test_load_project_context_returns_none_when_empty(tmp_path):
    """Empty / whitespace-only CONTEXT.md → treat as missing."""
    (tmp_path / "CONTEXT.md").write_text("   \n  \n", encoding="utf-8")
    assert sc._load_project_context(tmp_path) is None


def test_load_project_context_returns_section_when_present(tmp_path):
    """Normal CONTEXT.md → wrapped in '## Project Context' header, no truncation."""
    body = "# CONTEXT\n\nGlossary entry: **Pillar** — multi-sprint capability."
    (tmp_path / "CONTEXT.md").write_text(body, encoding="utf-8")
    out = sc._load_project_context(tmp_path)
    assert out is not None
    assert out.startswith("## Project Context\n")
    assert "Glossary entry: **Pillar**" in out
    assert "_(truncated" not in out


def test_load_project_context_truncates_at_byte_cap(tmp_path):
    """File larger than _CONTEXT_MAX_BYTES → truncated with note."""
    cap = sc._CONTEXT_MAX_BYTES
    # ASCII payload — bytes == chars, easy to reason about.
    body = "# CONTEXT\n\n" + ("x" * (cap + 500))
    (tmp_path / "CONTEXT.md").write_text(body, encoding="utf-8")
    out = sc._load_project_context(tmp_path)
    assert out is not None
    assert "_(truncated — full file at `CONTEXT.md`)_" in out
    # The injected text (excluding header + truncation note) should not exceed
    # the cap. Strip our wrapper to measure the body we actually emitted.
    inner = out[len("## Project Context\n"):].split("\n\n_(truncated")[0]
    assert len(inner.encode("utf-8")) <= cap


def test_load_project_context_byte_cap_handles_multibyte(tmp_path):
    """Multi-byte UTF-8 (Cyrillic) cut at byte boundary doesn't produce U+FFFD."""
    cap = sc._CONTEXT_MAX_BYTES
    # Cyrillic each char = 2 bytes in UTF-8, so 5000 chars = 10000 bytes > cap.
    body = "# CONTEXT\n\n" + ("я" * 5000)
    (tmp_path / "CONTEXT.md").write_text(body, encoding="utf-8")
    out = sc._load_project_context(tmp_path)
    assert out is not None
    assert "_(truncated" in out
    # No replacement-char artefact from a mid-codepoint cut.
    assert "�" not in out
    inner = out[len("## Project Context\n"):].split("\n\n_(truncated")[0]
    assert len(inner.encode("utf-8")) <= cap


def test_load_project_context_resolves_per_project_root(tmp_path):
    """A CONTEXT.md in one project root must NOT leak into a different one.

    Regression for PR #489 review: previously _load_project_context used the
    jarvis repo path unconditionally, so a redrobot session would inject
    jarvis's CONTEXT.md.
    """
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / "CONTEXT.md").write_text("# Project A context", encoding="utf-8")
    # proj_b deliberately has NO CONTEXT.md
    assert "Project A" in sc._load_project_context(proj_a)
    assert sc._load_project_context(proj_b) is None


# ---------------------------------------------------------------------------
# _query_pending_review_count — session-start review reminder (#556)
# ---------------------------------------------------------------------------
class _FakeRpcResponse:
    """Mimics Supabase response with .execute() chaining."""

    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _FakeRpcClient:
    """Fake Supabase client with .rpc() support for _query_pending_review_count."""

    def __init__(self, rpc_data: dict | None = None):
        # rpc_data: mapping from RPC name to list of result rows
        self._rpc_data = dict(rpc_data or {})
        self.call_log: list[tuple] = []
        self.table_log: list[tuple] = []

    def rpc(self, name: str, params: dict):
        self.call_log.append((name, params))
        rows = self._rpc_data.get(name, [])
        return _FakeRpcResponse(rows)

    def table(self, name: str):
        self.table_log.append(name)
        # Minimal table stub — not used by _query_pending_review_count
        import types as _t
        return _t.SimpleNamespace()


class _FakeRpcClientError:
    """Fake client that raises on any RPC call — simulates unreachable backend."""

    def rpc(self, name: str, params: dict):
        raise RuntimeError(f"RPC {name} failed")

    def table(self, name: str):
        return _FakeRpcClient().table(name)


def test_pending_review_count_zero_when_no_data():
    """No pending candidates → return 0."""
    client = _FakeRpcClient({"memory_review_list": []})
    assert sc._query_pending_review_count(client, "jarvis") == 0


def test_pending_review_count_project_only():
    """Only project-specific candidates → count them, global is empty."""
    client = _FakeRpcClient({
        "memory_review_list": [{"id": "a"}, {"id": "b"}],
    })
    # Two calls: one for project, one for global. Both return total 2 rows
    # across both calls (4 total).
    assert sc._query_pending_review_count(client, "jarvis") == 4


def test_pending_review_count_no_project():
    """When project is None, only query global scope (project_filter='')."""
    client = _FakeRpcClient({
        "memory_review_list": [{"id": "a"}, {"id": "b"}],
    })
    assert sc._query_pending_review_count(client, None) == 2


def test_pending_review_count_no_project_calls_empty_string():
    """Without a project, the global query uses project_filter='' (not None)."""
    client = _FakeRpcClient({"memory_review_list": []})
    sc._query_pending_review_count(client, None)
    rpc_calls = client.call_log
    assert len(rpc_calls) == 1
    name, params = rpc_calls[0]
    assert name == "memory_review_list"
    assert params.get("project_filter") == ""


def test_pending_review_count_with_project_calls_both_scopes():
    """With a project, two queries: project-specific + global ('')."""
    client = _FakeRpcClient({"memory_review_list": []})
    sc._query_pending_review_count(client, "redrobot")
    rpc_calls = client.call_log
    assert len(rpc_calls) == 2
    scopes = [c[1].get("project_filter") for c in rpc_calls]
    assert "redrobot" in scopes
    assert "" in scopes


def test_pending_review_count_graceful_on_rpc_error():
    """RPC failure → graceful degradation: returns 0, prints to stderr."""
    client = _FakeRpcClientError()
    assert sc._query_pending_review_count(client, "jarvis") == 0


def test_pending_review_count_graceful_on_rpc_error_no_project():
    """RPC failure with no active project → returns 0."""
    client = _FakeRpcClientError()
    assert sc._query_pending_review_count(client, None) == 0
