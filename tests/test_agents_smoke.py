"""Smoke tests for the Pillar 7 agents package.

These are import/config-level sanity checks — no Postgres, no Ollama.
Full end-to-end validation is manual (see docs/agents/langgraph-setup.md).
"""

from __future__ import annotations

import os

import pytest


def _require_httpx() -> None:
    """Skip if real ``httpx`` isn't installed.

    ``importorskip`` alone is too lenient: ``test_memory_server.py`` may put
    an empty stub module in ``sys.modules`` when real httpx is missing, so
    ``import httpx`` succeeds but the stub has no ``get`` attribute — and
    ``monkeypatch.setattr(github_client.httpx, "get", ...)`` then raises
    ``AttributeError``. Check for ``get`` to detect the stub case and skip
    properly.
    """
    hx = pytest.importorskip("httpx")
    if not hasattr(hx, "get"):
        pytest.skip("real httpx not installed (only stub present)")


def _require_supabase() -> None:
    """Skip if real ``supabase`` isn't installed.

    Same rationale as ``_require_httpx``: ``test_memory_server.py`` may put
    an empty stub module in ``sys.modules`` when real supabase is missing,
    so ``import supabase`` succeeds but later ``from supabase import
    Client`` fails with an unhelpful ImportError. Check for ``Client`` to
    detect the stub case and skip properly.
    """
    sb = pytest.importorskip("supabase")
    if not hasattr(sb, "Client"):
        pytest.skip("real supabase not installed (only stub present)")


def test_config_loads_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env overrides, config exposes the documented defaults."""
    for var in (
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "AGENTS_POSTGRES_URL",
        "SUPABASE_URL",
        "SUPABASE_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    from agents.config import (
        DEFAULT_OLLAMA_HOST,
        DEFAULT_OLLAMA_MODEL,
        DEFAULT_POSTGRES_URL,
        load_config,
    )

    cfg = load_config()
    assert cfg.ollama_host == DEFAULT_OLLAMA_HOST
    assert cfg.ollama_model == DEFAULT_OLLAMA_MODEL
    assert cfg.postgres_url == DEFAULT_POSTGRES_URL
    # Supabase bridge has no safe default — empty string means "not configured".
    assert cfg.supabase_url == ""
    assert cfg.supabase_key == ""


def test_config_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override the defaults."""
    monkeypatch.setenv("OLLAMA_HOST", "http://example:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("AGENTS_POSTGRES_URL", "postgresql://u:p@host:5432/db?sslmode=disable")
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon-key-xyz")

    from agents.config import load_config

    cfg = load_config()
    assert cfg.ollama_host == "http://example:11434"
    assert cfg.ollama_model == "llama3.2:3b"
    assert cfg.postgres_url.startswith("postgresql://u:p@host")
    assert cfg.supabase_url == "https://proj.supabase.co"
    assert cfg.supabase_key == "anon-key-xyz"


def test_ollama_client_defaults_to_think_false() -> None:
    """Shared chat wrapper must default `think=False` for Qwen3 safety."""
    pytest.importorskip("ollama")

    import inspect

    from agents.ollama_client import chat

    sig = inspect.signature(chat)
    assert sig.parameters["think"].default is False, (
        "think must default to False — see docs/agents/ollama-setup.md "
        "for the Qwen3 empty-response gotcha"
    )


def test_env_example_documents_agent_vars() -> None:
    """`.env.example` must document the new agent variables."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    with open(os.path.join(root, ".env.example"), encoding="utf-8") as f:
        content = f.read()
    # Pillar 7 agent-only vars + shared Supabase vars used by the bridge.
    for key in (
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "AGENTS_POSTGRES_URL",
        "SUPABASE_URL",
        "SUPABASE_KEY",
    ):
        assert key in content, f"{key} missing from .env.example"


def test_supabase_client_errors_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_client` must fail loudly when SUPABASE_URL/KEY are missing.

    Silently defaulting would let a misconfigured agent run against nothing
    and look healthy until the first write failed.
    """
    _require_supabase()
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    from agents.supabase_client import get_client

    with pytest.raises(RuntimeError, match="SUPABASE_URL and SUPABASE_KEY"):
        get_client()


def test_supabase_client_surface() -> None:
    """The bridge must expose the read/write helpers #173 requires."""
    _require_supabase()

    from agents import supabase_client as sb

    # Reads
    assert callable(sb.list_memories)
    assert callable(sb.list_events)
    assert callable(sb.list_goals)
    # Writes
    assert callable(sb.store_event)
    assert callable(sb.mark_event_processed)
    assert callable(sb.update_goal_progress)
    assert callable(sb.audit)


def test_github_client_event_allowlist() -> None:
    """Allow-list includes the six event types the monitor acts on."""
    _require_httpx()
    from agents.github_client import RELEVANT_EVENT_TYPES

    for needed in (
        "IssuesEvent",
        "PullRequestEvent",
        "PullRequestReviewEvent",
        "IssueCommentEvent",
        "PullRequestReviewCommentEvent",
        "PushEvent",
    ):
        assert needed in RELEVANT_EVENT_TYPES
    # Things we intentionally drop as noise.
    assert "WatchEvent" not in RELEVANT_EVENT_TYPES
    assert "ForkEvent" not in RELEVANT_EVENT_TYPES


class _Resp:
    """Minimal stand-in for an ``httpx.Response`` — used by the fetch tests."""

    def __init__(self, data: list[dict[str, object]]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, object]]:
        return self._data


def test_fetch_repo_events_slices_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """When new events outnumber ``limit``, pick the oldest N so the
    monitor's cursor advance stays contiguous.

    Regression guard for the bug Copilot flagged on #179: GitHub returns
    newest-first; a naive ``filtered[:limit]`` picks the newest N and the
    monitor then advances the cursor to the max id — permanently skipping
    the older-but-still-new events that didn't fit.
    """
    _require_httpx()
    from agents import github_client

    # 12 relevant events, newest first — like the real /events endpoint.
    # 12 < per_page=100, so the first page is also the last page.
    raw = [
        {"id": str(i), "type": "IssuesEvent", "actor": {}, "repo": {}, "payload": {}}
        for i in range(20, 8, -1)
    ]

    monkeypatch.setattr(github_client.httpx, "get", lambda *a, **kw: _Resp(raw))

    out = github_client.fetch_repo_events("o/r", after_event_id="8", limit=5)

    # Must return the 5 OLDEST new events (ids 9–13), not the 5 newest (16–20).
    ids = [int(e["id"]) for e in out]
    assert ids == [9, 10, 11, 12, 13], ids


def test_fetch_repo_events_paginates_past_first_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """More than one page of relevant events: must keep reading until the
    cursor is crossed, then return the oldest N across all collected pages.

    Regression guard for the pagination bug Copilot flagged on #181: a
    single page of 30 meant events older than the first page (but still
    newer than the cursor) were permanently skipped once the cursor
    advanced to the newest-seen id.
    """
    _require_httpx()
    from agents import github_client

    # Simulate a 250-event backlog, newest-first. per_page=100, so this is
    # 3 pages: ids 250..151, 150..51, 50..1. Cursor = 0 → everything new.
    pages = [
        [
            {"id": str(i), "type": "IssuesEvent", "actor": {}, "repo": {}, "payload": {}}
            for i in range(250, 150, -1)
        ],
        [
            {"id": str(i), "type": "IssuesEvent", "actor": {}, "repo": {}, "payload": {}}
            for i in range(150, 50, -1)
        ],
        [
            {"id": str(i), "type": "IssuesEvent", "actor": {}, "repo": {}, "payload": {}}
            for i in range(50, 0, -1)
        ],
    ]
    calls: list[dict[str, object]] = []

    def fake_get(*args: object, **kwargs: object) -> _Resp:
        params = kwargs.get("params", {}) or {}
        calls.append(dict(params))
        page = int(params.get("page", 1))
        return _Resp(pages[page - 1])

    monkeypatch.setattr(github_client.httpx, "get", fake_get)

    out = github_client.fetch_repo_events("o/r", after_event_id=None, limit=5)

    # Oldest 5 across the whole 250-event backlog — only reachable via
    # page 3. A single-page implementation would return 146..150 instead.
    ids = [int(e["id"]) for e in out]
    assert ids == [1, 2, 3, 4, 5], ids
    # And we made it through all three pages.
    pages_requested = sorted(int(c.get("page", 1)) for c in calls)
    assert pages_requested == [1, 2, 3], pages_requested


def test_fetch_repo_events_stops_at_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pagination halts as soon as a page's oldest event id is <= cursor,
    since every subsequent page is strictly older.
    """
    _require_httpx()
    from agents import github_client

    # Page 1 has ids 120..21 (100 events); cursor=50 means the oldest on
    # page 1 (id=21) is already <= cursor, so page 2 must NOT be fetched.
    pages = [
        [
            {"id": str(i), "type": "IssuesEvent", "actor": {}, "repo": {}, "payload": {}}
            for i in range(120, 20, -1)
        ],
        [  # If we accidentally fetch this, test will catch via pages_requested.
            {"id": str(i), "type": "IssuesEvent", "actor": {}, "repo": {}, "payload": {}}
            for i in range(20, 0, -1)
        ],
    ]
    calls: list[dict[str, object]] = []

    def fake_get(*args: object, **kwargs: object) -> _Resp:
        params = kwargs.get("params", {}) or {}
        calls.append(dict(params))
        page = int(params.get("page", 1))
        return _Resp(pages[page - 1] if page - 1 < len(pages) else [])

    monkeypatch.setattr(github_client.httpx, "get", fake_get)

    out = github_client.fetch_repo_events("o/r", after_event_id="50", limit=5)

    # Matches are ids 51..120; oldest 5 of those are 51..55.
    ids = [int(e["id"]) for e in out]
    assert ids == [51, 52, 53, 54, 55], ids
    # Must have stopped at page 1 — page 2 would only yield events older
    # than the cursor, so fetching it is wasted rate-limit budget.
    pages_requested = sorted(int(c.get("page", 1)) for c in calls)
    assert pages_requested == [1], pages_requested


def test_summarise_event_covers_known_types() -> None:
    """Every event-type branch produces a string with actor + repo + detail."""
    _require_httpx()
    from agents.github_client import summarise_event

    common = {"actor": {"login": "alice"}, "repo": {"name": "o/r"}}
    cases = [
        {
            **common,
            "type": "IssuesEvent",
            "payload": {"action": "opened", "issue": {"number": 42, "title": "bug"}},
        },
        {
            **common,
            "type": "PullRequestEvent",
            "payload": {"action": "opened", "pull_request": {"number": 7, "title": "feat"}},
        },
        {
            **common,
            "type": "PullRequestReviewEvent",
            "payload": {"review": {"state": "approved"}, "pull_request": {"number": 7}},
        },
        {
            **common,
            "type": "IssueCommentEvent",
            "payload": {"action": "created", "issue": {"number": 42}},
        },
        {
            **common,
            "type": "PushEvent",
            "payload": {"ref": "refs/heads/main", "commits": [{}, {}]},
        },
        {**common, "type": "WeirdEvent", "payload": {}},  # fallback branch
    ]
    for event in cases:
        s = summarise_event(event)
        assert "alice" in s, s
        assert "o/r" in s, s
        assert event["type"] in s, s


# NOTE: tests for agents.event_monitor (LangGraph monitor graph + classifier
# schema) were removed with the module in #744 — the deterministic router
# (agents/orchestrator.py) replaced the graph runtime. Router coverage lives
# in tests/test_orchestrator.py.
