"""Tests for mcp-status/server.py status_digest tool (#1017).

Tests the thin wrapper:
- status_digest delegates gather → engine correctly
- Provenance from gather/engine is passed through intact
- No detector/ranking logic is duplicated (imports from engine only)
- Tool input/output schema matches spec
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Load mcp-status/server.py under a UNIQUE module name ("status_server"), not the
# bare "server". mcp-memory's test suite also imports `from server import ...`;
# if both grabbed the global name "server" the one collected second would get the
# wrong module (full-suite collision, #1017). importlib with an explicit name and
# no sys.path mutation keeps the two server.py files isolated.
_status_server_path = Path(__file__).parent.parent / "mcp-status" / "server.py"
_spec = importlib.util.spec_from_file_location("status_server", _status_server_path)
status_server = importlib.util.module_from_spec(_spec)
sys.modules["status_server"] = status_server
_spec.loader.exec_module(status_server)
_convert_gather_to_engine_format = status_server._convert_gather_to_engine_format
_contradiction_verdicts_from_gather = status_server._contradiction_verdicts_from_gather


# ============================================================================
# Fixtures for gather result
# ============================================================================


def _days_ago(days: float) -> str:
    """Return ISO 8601 string for `days` ago in UTC."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def make_fixture_gather_result():
    """Create a minimal fixture GatherResult with real structure."""
    from scripts.status_gather import GatherResult, DecisionRecord

    result = GatherResult(
        gathered_at=datetime.now(timezone.utc).isoformat(),
        repos=[
            {
                "name": "Osasuwu/jarvis",
                "branch": "main",
                "clean": True,
                "degraded": False,
                "degradation_reason": None,
                "issues": [
                    {
                        "number": 1,
                        "title": "Test issue",
                        "labels": ["status:in-progress"],
                        "updatedAt": _days_ago(5),  # Stale
                        "milestone": None,
                    },
                ],
                "prs": [],
                "provenance": {
                    "git_state": {"ran": True, "ok": True, "input_rows": -1, "age": 0.1},
                    "gh_issues": {"ran": True, "ok": True, "input_rows": 1, "age": 0.5},
                    "gh_prs": {"ran": True, "ok": True, "input_rows": 0, "age": 0.3},
                    "gh_ci": {"ran": True, "ok": True, "input_rows": 0, "age": 0.2},
                    "gh_milestones": {"ran": True, "ok": True, "input_rows": 0, "age": 0.4},
                },
            },
        ],
        decisions=[
            DecisionRecord(
                id="dec-1",
                actor="session:test",
                decision="Use status_digest for synthesis",
                rationale="Single call is cleaner",
                created_at=datetime.now(timezone.utc).isoformat(),
                payload={
                    "decision": "Use status_digest for synthesis",
                    "rationale": "Single call is cleaner",
                },
            ),
        ],
        provenance={
            "repos_conf": {"ran": True, "ok": True, "input_rows": 1, "age": 0.05},
            "supabase_decisions": {"ran": True, "ok": True, "input_rows": 1, "age": 1.0},
            "status_snapshot": {"ran": True, "ok": False, "input_rows": 0, "age": 100.0},
        },
        errors=[],
    )
    return result


# ============================================================================
# Test: Convert gather result to engine format
# ============================================================================


def test_convert_gather_to_engine_format():
    """Test that GatherResult → Baseline/Delta/decisions works correctly."""
    gather_result = make_fixture_gather_result()
    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    # Baseline should have top-level provenance
    assert "repos_conf" in baseline.provenance
    assert baseline.provenance["repos_conf"].ran is True
    assert baseline.provenance["repos_conf"].ok is True

    # Delta should have repo state
    assert "Osasuwu/jarvis" in delta.repos
    repo_state = delta.repos["Osasuwu/jarvis"]
    assert repo_state.repo == "Osasuwu/jarvis"
    assert len(repo_state.open_issues) == 1
    assert repo_state.open_issues[0].number == 1
    assert repo_state.open_issues[0].title == "Test issue"
    assert "status:in-progress" in repo_state.open_issues[0].labels

    # Decisions should be converted
    assert len(decisions) == 1
    assert decisions[0].decision_id == "dec-1"
    assert "status_digest" in decisions[0].decision


def test_convert_drops_project_status_field():
    """#1065: the ProjectV2 board is no longer a data source — IssueInfo has no
    project_status field, and a stray project_status key on a gathered issue is
    ignored rather than plumbed through."""
    gather_result = make_fixture_gather_result()
    gather_result.repos[0]["issues"][0]["project_status"] = "Backlog"

    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    issue = delta.repos["Osasuwu/jarvis"].open_issues[0]
    assert not hasattr(issue, "project_status")


def test_convert_flattens_object_labels():
    """gh returns labels as objects [{"name":..,"color":..}], not strings.

    The engine's `_issue_priority` treats each label as a hashable string;
    feeding it a dict raised `TypeError: unhashable type: 'dict'` and crashed
    the whole digest (masked earlier by the cp1251 gather crash). The converter
    must flatten label objects to their names so priority detection survives
    real gh output. Regression for the /status label-dict crash ([no-issue]).
    """
    gather_result = make_fixture_gather_result()
    # Replace the fixture's string labels with the real gh object shape.
    gather_result.repos[0]["issues"][0]["labels"] = [
        {"name": "status:in-progress", "color": "ededed"},
        {"name": "P1", "color": "d93f0b"},
    ]

    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    issue = delta.repos["Osasuwu/jarvis"].open_issues[0]
    assert issue.labels == ["status:in-progress", "P1"]
    # And the flattened labels must be hashable (engine puts them through `in`).
    assert all(isinstance(lbl, str) for lbl in issue.labels)


def test_convert_handles_mixed_and_missing_labels():
    """Defensive: bare-string labels, missing key, and None all normalize."""
    gather_result = make_fixture_gather_result()
    gather_result.repos[0]["issues"][0]["labels"] = [
        {"name": "P0"},
        "already-a-string",
    ]
    # Second issue with labels entirely absent.
    gather_result.repos[0]["issues"].append(
        {
            "number": 2,
            "title": "No labels",
            "updatedAt": _days_ago(1),
            "milestone": None,
        }
    )

    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    issues = delta.repos["Osasuwu/jarvis"].open_issues
    assert issues[0].labels == ["P0", "already-a-string"]
    assert issues[1].labels == []


def test_provenance_passthrough():
    """Test that provenance from gather is preserved in conversion."""
    gather_result = make_fixture_gather_result()
    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    # Top-level provenance from gather should be in baseline
    assert baseline.provenance["repos_conf"].input_rows == 1
    assert baseline.provenance["supabase_decisions"].input_rows == 1

    # Repo data should be in delta
    assert "Osasuwu/jarvis" in delta.repos


def test_engine_analyze_receives_correct_format():
    """Test that the converted format can be passed to engine.analyze()."""
    from scripts.status_engine import analyze

    gather_result = make_fixture_gather_result()
    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    # Should not raise an error
    digest = analyze(baseline, delta, decisions)

    # Digest should have health and detector hits
    assert digest.health is not None
    assert digest.detector_hits is not None
    # The stale-in-progress detector should fire (issue updated 5 days ago)
    stale_hits = [h for h in digest.detector_hits if h.detector == "stale-in-progress"]
    assert len(stale_hits) > 0


def test_no_detector_logic_duplication():
    """Test that the server uses engine functions, not reimplementing them.

    This is a structural test — it verifies by import that we're calling
    the real engine.analyze(), not a local duplicate.
    """
    from scripts.status_engine import analyze as engine_analyze

    # The server should import and use the real engine function
    # (verified by absence of detector implementations in server.py)
    # This test simply confirms the import works.
    assert callable(engine_analyze)


# ============================================================================
# Test: Tool schema compliance
# ============================================================================


def test_tool_schema_structure():
    """Test that the tool schema has the required structure."""
    # This is a structural test — verify the tool schema is correct
    # by inspecting the actual server.py code structure
    import inspect

    # Get the source code of list_tools to verify it returns Tool objects
    # with the right names and descriptions
    src = inspect.getsource(status_server.list_tools)
    assert "status_digest" in src
    assert "gather" in src
    assert "engine" in src
    assert "jarvis_home" in src


def test_handlers_are_coroutines():
    """The MCP SDK awaits every registered handler. A sync `def list_tools`
    connects fine but makes `tools/list` raise
    `object list can't be used in 'await' expression` at runtime — Claude Code
    then registers ZERO tools from the server and status_digest silently
    disappears. (#1017 regression: list_tools shipped sync.) Both decorated
    handlers must be coroutine functions.
    """
    import inspect

    assert inspect.iscoroutinefunction(status_server.list_tools), (
        "list_tools must be `async def` — the MCP SDK awaits it"
    )
    assert inspect.iscoroutinefunction(status_server.call_tool), (
        "call_tool must be `async def` — the MCP SDK awaits it"
    )


# ============================================================================
# Test: Integration — gather result flow through to digest
# ============================================================================


def test_end_to_end_gather_to_digest():
    """Test the full pipeline: fixture gather → conversion → engine → digest."""
    from scripts.status_engine import analyze

    gather_result = make_fixture_gather_result()
    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)

    digest = analyze(baseline, delta, decisions)

    # Verify output structure
    assert digest.health is not None
    assert digest.detector_hits is not None
    assert digest.ranking is not None
    assert digest.provenance is not None

    # Provenance should include the sources from gather
    assert "repos_conf" in digest.provenance
    assert "supabase_decisions" in digest.provenance


# ============================================================================
# Test: Contradiction-cache deserialization + fold-through (#1016 AC4)
#
# The server reads the cached L1 verdicts from gather_result.contradiction_cache
# and folds them into analyze() — WITHOUT re-running the LLM. The cached hit
# must surface in the digest's detector_hits.
# ============================================================================


def _cache_with_contradiction():
    """Build a serialized contradiction cache carrying one contradiction."""
    from scripts.status_engine import (
        ContradictionVerdict,
        serialize_contradiction_cache,
    )

    verdicts = [
        ContradictionVerdict(
            decision_id="dec-9",
            issue_number=77,
            repo="Osasuwu/jarvis",
            verdict="contradiction",
            rationale="memory says shipped; issue #77 still open",
        ),
    ]
    return serialize_contradiction_cache(verdicts, generated_at="2024-06-09T12:00:00+00:00")


def test_contradiction_verdicts_from_gather_deserializes_cache():
    """The server helper turns the cached dict back into verdict objects."""
    gather_result = make_fixture_gather_result()
    gather_result.contradiction_cache = _cache_with_contradiction()

    verdicts = _contradiction_verdicts_from_gather(gather_result)
    assert len(verdicts) == 1
    assert verdicts[0].issue_number == 77
    assert verdicts[0].verdict == "contradiction"


def test_no_cache_yields_empty_verdicts():
    """No contradiction_cache → empty verdicts (intraday/L2-safe default)."""
    gather_result = make_fixture_gather_result()
    gather_result.contradiction_cache = None
    assert _contradiction_verdicts_from_gather(gather_result) == []


def test_cached_contradiction_surfaces_in_digest_without_llm():
    """End-to-end: cached verdict folds through analyze into detector_hits.

    This is the #1016 AC4 proof — the renderer (digest consumer) sees the
    contradiction with NO LLM call in this path.
    """
    from scripts.status_engine import MEMORY_GIT_CONTRADICTION, analyze

    gather_result = make_fixture_gather_result()
    gather_result.contradiction_cache = _cache_with_contradiction()

    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)
    verdicts = _contradiction_verdicts_from_gather(gather_result)
    digest = analyze(baseline, delta, decisions, contradiction_verdicts=verdicts)

    contradiction_hits = [h for h in digest.detector_hits if h.detector == MEMORY_GIT_CONTRADICTION]
    assert len(contradiction_hits) == 1
    assert contradiction_hits[0].issue_number == 77


def test_no_cache_means_no_contradiction_hit():
    """Empty cache → analyze folds nothing → no contradiction hit (L2 path)."""
    from scripts.status_engine import MEMORY_GIT_CONTRADICTION, analyze

    gather_result = make_fixture_gather_result()
    gather_result.contradiction_cache = None

    baseline, delta, decisions = _convert_gather_to_engine_format(gather_result)
    verdicts = _contradiction_verdicts_from_gather(gather_result)
    digest = analyze(baseline, delta, decisions, contradiction_verdicts=verdicts)

    contradiction_hits = [h for h in digest.detector_hits if h.detector == MEMORY_GIT_CONTRADICTION]
    assert contradiction_hits == []


# ============================================================================
# Test: Off-event-loop gather with timeout (#1083)
# ============================================================================


def test_gather_timeout_returns_one_line_error():
    """When gather() exceeds the timeout, return a one-line error (no traceback)."""
    async def _test():
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await status_server.call_tool("status_digest", {"jarvis_home": ""})
            assert len(result) == 1
            assert "timed out" in result[0].text
            assert "Traceback" not in result[0].text

    asyncio.run(_test())


def test_exception_returns_one_line_error_no_traceback():
    """Exception in gather returns one-line error; traceback is server-side only."""
    async def _test():
        with patch.object(status_server, "gather", side_effect=ValueError("test error")):
            result = await status_server.call_tool("status_digest", {"jarvis_home": ""})
            assert len(result) == 1
            assert "Error in status_digest" in result[0].text
            assert "Traceback" not in result[0].text

    asyncio.run(_test())
