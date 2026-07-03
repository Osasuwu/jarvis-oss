"""Tests for status_gather I/O adapter (#1015).

Test targets (per issue AC):
- Silent-empty guards: empty vs crashed sources are stamped distinctly
- Per-repo degradation: your-second-repo missing milestones degrades only that repo
- Snapshot-gap tolerance: no baselines still returns a usable partial result
- Pure helper functions: parse_repos_conf, _make_decision_record

All tests use injected fixture-returning callbacks — no network, no live creds.
"""

from __future__ import annotations

import json

import scripts.status_gather as status_gather
from scripts.status_gather import (
    SourceKind,
    parse_repos_conf,
    _make_decision_record,
    _extract_contradiction_cache,
    _default_run_git,
    _default_run_gh,
    gather,
    gather_contradiction_cache,
)


# ============================================================================
# Fixture helpers (mirror test_rework_policy.py pattern)
# ============================================================================


def _fixture_repos_conf(content: str) -> callable:
    """Return a read_repos_conf_fn that returns parsed lines from a string."""
    return lambda path: parse_repos_conf(content)


def _fixture_device_json(data: dict | None) -> callable:
    """Return a read_device_json_fn returning the given dict or None."""
    return lambda path: data


def _fixture_run_git(result: dict) -> callable:
    """Return a run_git_fn that always returns the same result."""
    return lambda repo_path, args: result


def _fixture_run_gh(result: dict) -> callable:
    """Return a run_gh_fn that always returns the same result."""
    return lambda repo, args: result


def _fixture_query_supabase(rows: list[dict] | None) -> callable:
    """Return a query_supabase_fn returning rows, None, or empty.

    Args:
        rows: If None, simulates a failed query (network error).
              If [], simulates an empty- but-successful query.
              Otherwise returns the list as-is.
    """

    def _query(url: str, key: str, table: str, params: dict) -> list[dict] | None:
        if rows is None:
            return None  # failed query
        return rows  # possibly empty

    return _query


def _fixture_now() -> callable:
    """Return a now_fn with a fixed timestamp."""
    fixed = 1_717_000_000.0  # 2024-06-09T12:26:40 UTC
    return lambda: fixed


def _make_gh_success(data: list) -> dict:
    """Simulate a successful gh command returning JSON."""
    return {"stdout": json.dumps(data), "stderr": "", "returncode": 0}


def _make_gh_empty() -> dict:
    """Simulate a successful gh command with no output."""
    return {"stdout": "", "stderr": "", "returncode": 0}


def _make_gh_fail() -> dict:
    """Simulate a failed gh command."""
    return {"stdout": "", "stderr": "gh: not authenticated", "returncode": 1}


def _fixture_query_by_table(table_rows: dict) -> callable:
    """Return a table-AWARE query_supabase_fn.

    The default `_fixture_query_supabase` is table-blind (same rows for every
    table); that conflates the `episodes` (decisions) and `memories`
    (status-snapshot) queries. This fixture keys returned rows by table name so
    a test can supply a snapshot for `memories` without polluting `episodes`.
    A table absent from the mapping yields [] (empty-but-successful).
    """

    def _query(url: str, key: str, table: str, params: dict) -> list[dict] | None:
        return table_rows.get(table, [])

    return _query


def _snapshot_memory(
    generated_at: str = "2024-06-09T12:00:00+00:00", verdicts: list[dict] | None = None
) -> dict:
    """Build a `memories` row whose body carries a fenced yaml contradiction
    cache, mirroring what the status-record L1 audit writes."""
    verdicts = (
        verdicts
        if verdicts is not None
        else [
            {
                "decision_id": "d1",
                "issue_number": 42,
                "repo": "Osasuwu/jarvis",
                "verdict": "contradiction",
                "rationale": "memory says shipped; issue still open",
            },
        ]
    )
    lines = [
        "# Status snapshot 2024-06-09",
        "",
        "```yaml",
        "contradiction_cache:",
        "  schema: contradiction-cache/v1",
        f"  generated_at: '{generated_at}'",
        "  verdicts:",
    ]
    for v in verdicts:
        lines.append(f"    - decision_id: {v['decision_id']}")
        lines.append(f"      issue_number: {v['issue_number']}")
        lines.append(f"      repo: {v['repo']}")
        lines.append(f"      verdict: {v['verdict']}")
        lines.append(f"      rationale: {v['rationale']}")
    lines.append("```")
    return {
        "name": "status_snapshot_2024-06-09",
        "content": "\n".join(lines),
        "created_at": "2024-06-09T12:00:05+00:00",
    }


# ============================================================================
# Test: parse_repos_conf (pure function)
# ============================================================================


class TestParseReposConf:
    """parse_repos_conf — pure, directly tested."""

    def test_parses_owner_repo_lines(self):
        content = "Osasuwu/jarvis\nyour-username/your-second-repo\n"
        assert parse_repos_conf(content) == [
            "Osasuwu/jarvis",
            "your-username/your-second-repo",
        ]

    def test_skips_comments_and_blanks(self):
        content = "# This is a comment\n\nOsasuwu/jarvis\n\n  # another\nyour-username/your-second-repo\n"
        assert parse_repos_conf(content) == [
            "Osasuwu/jarvis",
            "your-username/your-second-repo",
        ]

    def test_returns_empty_for_empty_string(self):
        assert parse_repos_conf("") == []

    def test_returns_empty_for_only_comments(self):
        assert parse_repos_conf("# only comment\n# another") == []

    def test_strips_whitespace(self):
        content = "  Osasuwu/jarvis  \n  your-username/your-second-repo  \n"
        assert parse_repos_conf(content) == [
            "Osasuwu/jarvis",
            "your-username/your-second-repo",
        ]


# ============================================================================
# Test: _make_decision_record (pure function)
# ============================================================================


class TestMakeDecisionRecord:
    """_make_decision_record — pure row-to-record conversion."""

    def test_converts_full_row(self):
        row = {
            "id": "abc-123",
            "actor": "session:2026-06-01",
            "kind": "decision_made",
            "payload": json.dumps({"decision": "Use pydantic", "rationale": "Validation matters"}),
            "created_at": "2026-06-01T12:00:00Z",
        }
        # JSONB columns come back as dict from Supabase, not string
        row["payload"] = {"decision": "Use pydantic", "rationale": "Validation matters"}

        record = _make_decision_record(row)
        assert record.id == "abc-123"
        assert record.actor == "session:2026-06-01"
        assert record.decision == "Use pydantic"
        assert record.rationale == "Validation matters"
        assert record.created_at == "2026-06-01T12:00:00Z"

    def test_handles_empty_payload(self):
        row = {
            "id": "abc-123",
            "actor": "session:test",
            "kind": "decision_made",
            "payload": {},
            "created_at": "2026-06-01T12:00:00Z",
        }
        record = _make_decision_record(row)
        assert record.decision == ""
        assert record.rationale == ""

    def test_handles_missing_fields(self):
        row = {"id": "abc-123", "actor": "test", "created_at": "now"}
        record = _make_decision_record(row)
        assert record.decision == ""
        assert record.payload == {}


# ============================================================================
# Test: Silent-empty guards
# ============================================================================


class TestSilentEmptyGuards:
    """An empty result and a crashed/failed source are stamped distinctly.

    Empty result (e.g., no open issues): {ran=T, ok=T, input_rows=0}
    Crashed source (e.g., network error): {ran=T, ok=F, input_rows=0}

    Both have input_rows=0 but differ on ok — the renderer checks ok first.
    """

    def test_empty_gh_result_is_ok_with_zero_rows(self):
        """AC: a gh command returning empty list → ok=True, input_rows=0."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),  # empty list
            query_supabase_fn=_fixture_query_supabase([]),  # empty
            now_fn=_fixture_now(),
        )

        assert len(result.repos) == 1
        repo = result.repos[0]

        # PRs came back empty → ok=True, input_rows=0
        prs_prov = repo["provenance"][SourceKind.GH_PRS]
        assert prs_prov["ran"] is True
        assert prs_prov["ok"] is True  # empty is NOT a failure
        assert prs_prov["input_rows"] == 0

        # Issues came back empty → ok=True, input_rows=0
        issues_prov = repo["provenance"][SourceKind.GH_ISSUES]
        assert issues_prov["ran"] is True
        assert issues_prov["ok"] is True
        assert issues_prov["input_rows"] == 0

    def test_failed_gh_result_is_not_ok_with_zero_rows(self):
        """AC: a failing gh command → ok=False, input_rows=0."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_fail()),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        assert len(result.repos) == 1
        repo = result.repos[0]

        # All gh sources failed
        for source in [
            SourceKind.GH_PRS,
            SourceKind.GH_ISSUES,
            SourceKind.GH_CI,
            SourceKind.GH_MILESTONES,
        ]:
            prov = repo["provenance"][source]
            assert prov["ran"] is True, f"{source} should have ran=True"
            assert prov["ok"] is False, f"{source} should have ok=False"
            assert prov["input_rows"] == 0

    def test_empty_supabase_is_ok_with_zero_rows(self):
        """AC: Supabase returns [] → ok=True, input_rows=0."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase([]),  # empty list
            now_fn=_fixture_now(),
        )

        decisions_prov = result.provenance.get(SourceKind.SUPABASE_DECISIONS, {})
        assert decisions_prov["ran"] is True
        assert decisions_prov["ok"] is True  # empty is OK
        assert decisions_prov["input_rows"] == 0
        assert len(result.decisions) == 0

    def test_failed_supabase_is_not_ok(self):
        """AC: Supabase query returns None (network error) → ok=False."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase(None),  # failed
            now_fn=_fixture_now(),
        )

        decisions_prov = result.provenance.get(SourceKind.SUPABASE_DECISIONS, {})
        assert decisions_prov["ran"] is True
        assert decisions_prov["ok"] is False  # failure
        assert decisions_prov["input_rows"] == 0
        assert len(result.decisions) == 0

    def test_empty_repos_conf_is_not_ok(self):
        """AC: empty repos.conf → ok=False, input_rows=0, no repos gathered."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf(""),
            read_device_json_fn=_fixture_device_json(None),
            run_git_fn=_fixture_run_git({"stdout": "", "stderr": "", "returncode": -1}),
            run_gh_fn=_fixture_run_gh(_make_gh_empty()),
            query_supabase_fn=_fixture_query_supabase(None),
            now_fn=_fixture_now(),
        )

        assert result.provenance[SourceKind.REPOS_CONF]["ok"] is False
        assert result.provenance[SourceKind.REPOS_CONF]["input_rows"] == 0
        assert len(result.repos) == 0
        assert len(result.errors) > 0

    def test_provenance_distinguishes_empty_from_failed(self):
        """Regression: empty and failed must NOT produce identical provenance.

        The renderer relies on ok to gate green health. If both are stamped
        identically, an empty-but-healthy repo would show red, and a silently
        failed one would show green.
        """
        # Empty case
        empty_result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        # Failed case
        failed_result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_fail()),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        empty_prs = empty_result.repos[0]["provenance"][SourceKind.GH_PRS]
        failed_prs = failed_result.repos[0]["provenance"][SourceKind.GH_PRS]

        # Empty: ok=True, failed: ok=False
        assert empty_prs["ok"] is True
        assert failed_prs["ok"] is False
        # Both: input_rows=0
        assert empty_prs["input_rows"] == 0
        assert failed_prs["input_rows"] == 0
        # Both: ran=True
        assert empty_prs["ran"] is True
        assert failed_prs["ran"] is True


# ============================================================================
# Test: Per-repo degradation
# ============================================================================


class TestPerRepoDegradation:
    """your-second-repo asymmetry: a missing source on one repo degrades only that repo.

    your-second-repo has no Projects board, manual merge, possibly-down CI. A source
    missing on your-second-repo must degrade that repo's entry, not jarvis or the whole
    gather.
    """

    def test_second_repo_milestones_degrade_only_second_repo(self):
        """AC: your-second-repo milestones fail → your-second-repo degraded, jarvis unaffected."""
        # Jarvis gh succeeds, your-second-repo gh fails on milestones
        call_count: dict[str, int] = {}

        def _side_effect_gh(repo: str, args: list[str]) -> dict:
            call_count[repo] = call_count.get(repo, 0) + 1
            is_milestones = any("milestones" in a or "api" in a for a in args)
            if is_milestones and "your-second-repo" in repo:
                return _make_gh_fail()
            return _make_gh_success([{"number": 1, "title": "v1"}])

        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\nyour-username/your-second-repo\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_side_effect_gh,
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        assert len(result.repos) == 2

        jarvis_entry = [r for r in result.repos if "jarvis" in r["name"]][0]
        second_repo_entry = [r for r in result.repos if "your-second-repo" in r["name"]][0]

        # Jarvis: milestones ok, not degraded
        assert jarvis_entry["degraded"] is False
        assert jarvis_entry["degradation_reason"] is None
        assert jarvis_entry["provenance"][SourceKind.GH_MILESTONES]["ok"] is True
        assert len(jarvis_entry.get("milestones", [])) > 0

        # Redrobot: milestones failed, degraded
        assert second_repo_entry["degraded"] is True
        assert second_repo_entry["degradation_reason"] is not None
        assert "milestones" in second_repo_entry["degradation_reason"].lower()
        assert second_repo_entry["provenance"][SourceKind.GH_MILESTONES]["ok"] is False
        # Other your-second-repo sources still ok
        assert second_repo_entry["provenance"][SourceKind.GH_PRS]["ok"] is True
        assert second_repo_entry["provenance"][SourceKind.GH_ISSUES]["ok"] is True

    def test_second_repo_failure_does_not_affect_jarvis_prs(self):
        """AC: your-second-repo gh auth failure → jarvis PRs still gather correctly."""

        def _side_effect_gh(repo: str, args: list[str]) -> dict:
            if "your-second-repo" in repo:
                return _make_gh_fail()
            return _make_gh_success(
                [{"number": 42, "title": "Fix bug", "createdAt": "2026-06-01T00:00:00Z"}]
            )

        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\nyour-username/your-second-repo\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_side_effect_gh,
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        assert len(result.repos) == 2

        jarvis_entry = [r for r in result.repos if "jarvis" in r["name"]][0]
        second_repo_entry = [r for r in result.repos if "your-second-repo" in r["name"]][0]

        # Jarvis PRs ok
        assert jarvis_entry["provenance"][SourceKind.GH_PRS]["ok"] is True
        assert len(jarvis_entry.get("prs", [])) == 1
        assert jarvis_entry["prs"][0]["number"] == 42

        # Redrobot PRs failed, but jarvis unaffected
        assert second_repo_entry["provenance"][SourceKind.GH_PRS]["ok"] is False
        assert second_repo_entry.get("prs") is None or second_repo_entry["prs"] == []


# ============================================================================
# Test: Snapshot-gap tolerance
# ============================================================================


class TestSnapshotGapTolerance:
    """Gather must return a partial structure when no fresh baseline exists.

    Cron runs on the routine host only (not the primary workstation). A fresh L1 baseline may be
    absent. Gather must still return a usable, provenance-marked partial result.
    """

    def test_no_supabase_creds_returns_partial_result(self):
        """AC: SUPABASE_URL/SUPABASE_KEY unset → decisions source not ok,
        but gather still returns repos and errors list."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([{"number": 1}])),
            query_supabase_fn=_fixture_query_supabase(None),
            now_fn=_fixture_now(),
        )

        # Still got repos
        assert len(result.repos) == 1
        assert result.repos[0]["name"] == "Osasuwu/jarvis"

        # Decisions not ok (no creds)
        decisions_prov = result.provenance.get(SourceKind.SUPABASE_DECISIONS, {})
        assert decisions_prov.get("ran") is True
        assert decisions_prov.get("ok") is False

        # Errors recorded (non-fatal)
        assert len(result.errors) > 0

    def test_supabase_returns_real_data(self):
        """Happy path: supabase returns decisions → records parsed."""
        rows = [
            {
                "id": "d1",
                "actor": "session:test",
                "kind": "decision_made",
                "payload": {"decision": "Use X", "rationale": "Faster"},
                "created_at": "2026-06-01T00:00:00Z",
            },
            {
                "id": "d2",
                "actor": "session:test2",
                "kind": "decision_made",
                "payload": {"decision": "Drop Y", "rationale": "Unused"},
                "created_at": "2026-06-02T00:00:00Z",
            },
        ]

        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase(rows),
            now_fn=_fixture_now(),
        )

        assert len(result.decisions) == 2
        assert result.decisions[0].decision == "Use X"
        assert result.decisions[1].decision == "Drop Y"

        decisions_prov = result.provenance.get(SourceKind.SUPABASE_DECISIONS, {})
        assert decisions_prov["ok"] is True
        assert decisions_prov["input_rows"] == 2

    def test_status_snapshot_not_available(self):
        """AC: no status baselines → snapshot query ran and succeeded-empty
        (ok=True, cache None), but repos + decisions still gathered.

        Empty is NOT a failure (MAJOR fix, PR #1046): only a transport error
        is ok=False. The renderer reads "no snapshot" from cache is None /
        input_rows==0, not from ok."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        # Snapshot query ran and succeeded with no rows — empty, not failed.
        snap_prov = result.provenance.get(SourceKind.STATUS_SNAPSHOT, {})
        assert snap_prov.get("ran") is True
        assert snap_prov.get("ok") is True
        assert snap_prov.get("input_rows") == 0
        assert result.contradiction_cache is None

        # But repos and decisions are still gathered
        assert len(result.repos) == 1
        assert result.repos[0]["name"] == "Osasuwu/jarvis"

        # gathered_at is set
        assert result.gathered_at != ""


# ============================================================================
# Test: Provenance contract
# ============================================================================


class TestProvenanceContract:
    """Every source carries provenance. The result is serializable."""

    def test_all_sources_have_provenance(self):
        """Every SourceKind appears in the result."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([{"number": 1}])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        # Top-level sources
        for sk in [
            SourceKind.REPOS_CONF,
            SourceKind.SUPABASE_DECISIONS,
            SourceKind.STATUS_SNAPSHOT,
        ]:
            assert sk in result.provenance, f"Missing top-level provenance: {sk}"

        # Per-repo sources
        repo = result.repos[0]
        for sk in [
            SourceKind.GIT_STATE,
            SourceKind.GH_PRS,
            SourceKind.GH_ISSUES,
            SourceKind.GH_CI,
            SourceKind.GH_MILESTONES,
        ]:
            assert sk in repo["provenance"], f"Missing repo provenance: {sk}"

    def test_result_is_json_serializable(self):
        """GatherResult.to_dict() produces JSON-serializable output."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([{"number": 1}])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        serialized = json.dumps(result.to_dict(), default=str)
        parsed = json.loads(serialized)
        assert len(parsed["repos"]) == 1
        assert "provenance" in parsed
        assert "gathered_at" in parsed


# ============================================================================
# Test: Degradation flag when git state unavailable
# ============================================================================


class TestGitStateDegradation:
    """Git state marks provenance !ok when local repo path doesn't exist."""

    def test_no_local_repo_path_sets_git_not_ok(self):
        """AC: device.json has repos_path but repo dir missing → git !ok."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/nonexistent/path"}),
            run_git_fn=_fixture_run_git({"stdout": "", "stderr": "not found", "returncode": -1}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        repo = result.repos[0]
        git_prov = repo["provenance"][SourceKind.GIT_STATE]
        assert git_prov["ran"] is True
        assert git_prov["ok"] is False  # local dir not available
        assert repo["branch"] is None
        assert repo["clean"] is None


# ============================================================================
# Test: Contradiction-cache gather (#1016 AC3/AC4)
#
# The status-record L1 audit writes the LLM contradiction verdicts into the
# `status-snapshot`-tagged memory as a fenced yaml block. gather() reads that
# cache back WITHOUT re-running the LLM (AC4: "readable by the renderer without
# re-running the LLM"). These tests pin the read-back path.
# ============================================================================


class TestExtractContradictionCache:
    """Pure helper: pull the contradiction_cache dict out of a memory body."""

    def test_extracts_cache_from_fenced_yaml(self):
        snap = _snapshot_memory()
        cache = _extract_contradiction_cache(snap["content"])
        assert isinstance(cache, dict)
        assert cache["schema"] == "contradiction-cache/v1"
        assert len(cache["verdicts"]) == 1
        assert cache["verdicts"][0]["issue_number"] == 42

    def test_no_yaml_block_returns_none(self):
        cache = _extract_contradiction_cache("# Just a heading\n\nNo fenced block.")
        assert cache is None

    def test_yaml_without_cache_key_returns_none(self):
        body = "```yaml\nsomething_else: 1\n```"
        assert _extract_contradiction_cache(body) is None

    def test_malformed_yaml_returns_none(self):
        body = "```yaml\n  : : not valid : :\n```"
        assert _extract_contradiction_cache(body) is None

    def test_empty_content_returns_none(self):
        assert _extract_contradiction_cache("") is None

    def test_trailing_blank_line_before_fence(self):
        """A blank line before the closing fence (common Markdown emit) must not
        defeat extraction — the cache should still parse (M1)."""
        body = (
            "```yaml\n"
            "contradiction_cache:\n"
            "  schema: contradiction-cache/v1\n"
            "  verdicts: []\n"
            "\n"  # trailing blank line before the closing fence
            "```"
        )
        cache = _extract_contradiction_cache(body)
        assert isinstance(cache, dict)
        assert cache["schema"] == "contradiction-cache/v1"

    def test_fence_with_info_string_after_yaml(self):
        """A fence tagged ```yaml title=... still extracts (M1 regex tolerance)."""
        body = "```yaml extra-info\ncontradiction_cache:\n  verdicts: []\n```"
        assert _extract_contradiction_cache(body) is not None

    def test_multi_fence_skips_to_cache_block(self):
        """First yaml fence lacks contradiction_cache; the loop continues to the
        second fence that has it (N2 — finditer continuation)."""
        body = (
            "```yaml\n"
            "other_metadata: 1\n"
            "```\n\n"
            "```yaml\n"
            "contradiction_cache:\n"
            "  schema: contradiction-cache/v1\n"
            "  verdicts: []\n"
            "```"
        )
        cache = _extract_contradiction_cache(body)
        assert isinstance(cache, dict)
        assert cache["schema"] == "contradiction-cache/v1"


class TestGatherContradictionCache:
    """gather_contradiction_cache reads the latest status-snapshot memory."""

    def test_snapshot_with_cache_returns_ok(self):
        snap = _snapshot_memory()
        query = _fixture_query_by_table({"memories": [snap]})
        cache, prov = gather_contradiction_cache(
            "https://x",
            "k",
            query,
            now=1_717_000_000.0,
        )
        assert cache is not None
        assert cache["schema"] == "contradiction-cache/v1"
        assert prov.ran is True
        assert prov.ok is True
        assert prov.input_rows == 1  # one verdict

    def test_no_snapshot_row_is_ok_empty(self):
        # No snapshot yet (first-run device, or intraday before L1) is a
        # legitimate empty state, NOT a failure — mirrors gather_decisions
        # where an empty query result is ok=True. Only a transport failure
        # is ok=False. Conflating the two (MAJOR, PR #1046 re-review) hid a
        # Supabase outage behind an indistinguishable "no data" stamp.
        query = _fixture_query_by_table({"memories": []})
        cache, prov = gather_contradiction_cache(
            "https://x",
            "k",
            query,
            now=1_717_000_000.0,
        )
        assert cache is None
        assert prov.ran is True
        assert prov.ok is True
        assert prov.input_rows == 0

    def test_query_failure_returns_not_ok(self):
        def _failing(url, key, table, params):
            return None

        cache, prov = gather_contradiction_cache(
            "https://x",
            "k",
            _failing,
            now=1_717_000_000.0,
        )
        assert cache is None
        assert prov.ran is True
        assert prov.ok is False

    def test_provenance_distinguishes_empty_from_failed(self):
        # The whole point of the MAJOR fix: a caller must be able to tell a
        # Supabase outage (None) apart from a first-run empty result ([]).
        empty_q = _fixture_query_by_table({"memories": []})
        _, empty_prov = gather_contradiction_cache(
            "https://x",
            "k",
            empty_q,
            now=1_717_000_000.0,
        )

        def _failing(url, key, table, params):
            return None

        _, failed_prov = gather_contradiction_cache(
            "https://x",
            "k",
            _failing,
            now=1_717_000_000.0,
        )
        assert empty_prov.ok != failed_prov.ok
        assert empty_prov.ok is True
        assert failed_prov.ok is False

    def test_snapshot_without_cache_block_returns_not_ok(self):
        row = {
            "name": "status_snapshot_x",
            "content": "# Snapshot\n\nno cache here",
            "created_at": "2024-06-09T12:00:00+00:00",
        }
        query = _fixture_query_by_table({"memories": [row]})
        cache, prov = gather_contradiction_cache(
            "https://x",
            "k",
            query,
            now=1_717_000_000.0,
        )
        assert cache is None
        assert prov.ok is False

    def test_age_reflects_generated_at_not_query_time(self):
        # generated_at is 2024-06-09T12:00:00Z; now is +100s. Age must be the
        # data age (~100s), so the renderer's freshness gate is meaningful.
        snap = _snapshot_memory(generated_at="2024-06-09T12:00:00+00:00")
        query = _fixture_query_by_table({"memories": [snap]})
        gen_epoch = 1_717_934_400.0  # 2024-06-09T12:00:00 UTC
        cache, prov = gather_contradiction_cache(
            "https://x",
            "k",
            query,
            now=gen_epoch + 100.0,
        )
        assert prov.age is not None
        assert abs(prov.age - 100.0) < 2.0

    def test_query_filters_exclude_soft_deleted_and_poisoned_rows(self):
        # Security/integrity (PR #1046 re-review LOWs): a soft-deleted or
        # expired snapshot must not be folded, and a sandcastle-written row
        # (anon key, RLS-gated to source_provenance like 'sandcastle:%') must
        # not be trusted as an L1 baseline. Assert the query carries the
        # live-row + provenance filters so the DB never returns those rows.
        captured = {}

        def _capturing(url, key, table, params):
            captured.update(params)
            return []

        gather_contradiction_cache(
            "https://x",
            "k",
            _capturing,
            now=1_717_000_000.0,
        )
        assert captured.get("deleted_at") == "is.null"
        assert captured.get("expired_at") == "is.null"
        # NULL or non-sandcastle provenance passes; sandcastle:* is rejected.
        assert "sandcastle" in captured.get("or", "")
        assert "not.like" in captured.get("or", "")


class TestGatherIntegratesContradictionCache:
    """gather() Step 5 populates result.contradiction_cache from the snapshot."""

    def test_gather_populates_contradiction_cache(self):
        snap = _snapshot_memory()
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_by_table({"memories": [snap]}),
            now_fn=_fixture_now(),
        )
        assert result.contradiction_cache is not None
        assert result.contradiction_cache["schema"] == "contradiction-cache/v1"
        snap_prov = result.provenance[SourceKind.STATUS_SNAPSHOT]
        assert snap_prov["ran"] is True
        assert snap_prov["ok"] is True

    def test_gather_no_snapshot_leaves_cache_none(self):
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )
        assert result.contradiction_cache is None
        snap_prov = result.provenance[SourceKind.STATUS_SNAPSHOT]
        # Empty snapshot query succeeds with no rows (ok=True); cache is None.
        # ok=False is reserved for a transport failure (MAJOR fix, PR #1046).
        assert snap_prov["ok"] is True
        assert snap_prov["input_rows"] == 0

    def test_contradiction_cache_is_json_serializable(self):
        snap = _snapshot_memory()
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_by_table({"memories": [snap]}),
            now_fn=_fixture_now(),
        )
        parsed = json.loads(json.dumps(result.to_dict(), default=str))
        assert parsed["contradiction_cache"]["schema"] == "contradiction-cache/v1"


# ============================================================================
# Test: default subprocess runners decode as UTF-8 (Windows cp1251 crash)
#
# On Windows, subprocess.run(text=True) without an explicit encoding decodes
# child stdout with the locale codec (cp1251 here). your-second-repo issue titles are
# Cyrillic; gh emits UTF-8 bytes, so the cp1251 decode raised
# `UnicodeDecodeError` and crashed the whole /status gather. The default git/gh
# runners must pin encoding="utf-8", errors="replace". [no-issue] regression.
# ============================================================================


class TestDefaultRunnersUtf8:
    """_default_run_git / _default_run_gh must decode child output as UTF-8."""

    def _capture_run(self, monkeypatch):
        """Patch subprocess.run in the gather module, capture its kwargs."""
        captured: dict = {}

        class _FakeCompleted:
            stdout = "тест"  # Cyrillic — only survives a UTF-8 decode
            stderr = ""
            returncode = 0

        def _fake_run(*args, **kwargs):
            captured.update(kwargs)
            return _FakeCompleted()

        monkeypatch.setattr(status_gather.subprocess, "run", _fake_run)
        return captured

    def test_run_git_requests_utf8(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        out = _default_run_git("/repo", ["status"])
        assert captured.get("encoding") == "utf-8"
        assert captured.get("errors") == "replace"
        assert out["stdout"] == "тест"

    def test_run_gh_requests_utf8(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        out = _default_run_gh("Owner/repo", ["issue", "list"])
        assert captured.get("encoding") == "utf-8"
        assert captured.get("errors") == "replace"
        assert out["stdout"] == "тест"


class TestDefaultRunnersDetachStdin:
    """_default_run_git / _default_run_gh must pass stdin=DEVNULL.

    Regression: when gather() runs inside the mcp-status stdio server, the
    process's stdin (fd 0) is the MCP transport pipe from the client. A child
    gh/git spawned without stdin=DEVNULL inherits that pipe; its own background
    grandchildren keep the pipe's write end open, so subprocess.run's
    communicate() can't reach EOF and every call stalls toward its timeout —
    turning a ~10s gather into ~70s (observed via the status_digest MCP tool).
    Detaching stdin severs the inheritance. Same class of bug as the memory
    lesson `subprocess_capture_output_grandchild_pipe_hang`.
    """

    def _capture_run(self, monkeypatch):
        captured: dict = {}

        class _FakeCompleted:
            stdout = ""
            stderr = ""
            returncode = 0

        def _fake_run(*args, **kwargs):
            captured.update(kwargs)
            return _FakeCompleted()

        monkeypatch.setattr(status_gather.subprocess, "run", _fake_run)
        return captured

    def test_run_git_detaches_stdin(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        _default_run_git("/repo", ["status"])
        assert captured.get("stdin") is status_gather.subprocess.DEVNULL

    def test_run_gh_detaches_stdin(self, monkeypatch):
        captured = self._capture_run(monkeypatch)
        _default_run_gh("Owner/repo", ["issue", "list"])
        assert captured.get("stdin") is status_gather.subprocess.DEVNULL


class TestDefaultRunGhRepoFlag:
    """_default_run_gh targets the repo correctly per subcommand.

    Regression: `gh api` addresses the repo via the URL path and rejects a
    trailing `--repo` flag ("unknown flag: --repo"), which made every milestone
    gather fail (ok=False) and the digest degrade silently. Every other gh
    subcommand still needs `--repo`.
    """

    def _capture_cmd(self, monkeypatch):
        captured: dict = {}

        class _FakeCompleted:
            stdout = "[]"
            stderr = ""
            returncode = 0

        def _fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return _FakeCompleted()

        monkeypatch.setattr(status_gather.subprocess, "run", _fake_run)
        return captured

    def test_api_subcommand_omits_repo_flag(self, monkeypatch):
        captured = self._capture_cmd(monkeypatch)
        _default_run_gh("Owner/repo", ["api", "repos/Owner/repo/milestones"])
        assert "--repo" not in captured["cmd"]
        assert captured["cmd"] == ["gh", "api", "repos/Owner/repo/milestones"]

    def test_non_api_subcommand_appends_repo_flag(self, monkeypatch):
        captured = self._capture_cmd(monkeypatch)
        _default_run_gh("Owner/repo", ["issue", "list"])
        assert captured["cmd"][-2:] == ["--repo", "Owner/repo"]


# ============================================================================
# Test: status:* labels flow through to issues (#1065)
# ============================================================================
#
# Grill #1065 (decision 0a02d3ee) removed the ProjectV2 board fetch from the
# gather path: status:* labels are the single source of truth and detectors read
# them off the issues gathered here. There is no more `project_status` field, no
# `gh_projects` provenance source, and no `read_repos_conf_projects_fn` param.


def _labels_gh(issues: list[dict]):
    """Arg-aware run_gh: returns the given issues for `issue list`, empty-ok
    for everything else. No graphql branch — the board is never fetched now.
    """

    def _run(repo: str, args: list[str]) -> dict:
        if "issue" in args:
            return _make_gh_success(issues)
        return _make_gh_empty()

    return _run


class TestLabelsFlowThrough:
    """#1065 — status:* labels reach the gathered issues; board is not fetched."""

    def test_labels_preserved_on_issues(self):
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_labels_gh(
                [
                    {
                        "number": 42,
                        "title": "Backlog issue",
                        "labels": [],
                        "updatedAt": "2024-05-01T00:00:00Z",
                    },
                    {
                        "number": 7,
                        "title": "Ready issue",
                        "labels": [{"name": "status:ready"}],
                        "updatedAt": "2024-05-01T00:00:00Z",
                    },
                ]
            ),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        jarvis = result.repos[0]
        by_num = {i["number"]: i for i in jarvis["issues"]}
        assert by_num[42]["labels"] == []
        assert by_num[7]["labels"] == [{"name": "status:ready"}]

    def test_no_project_status_field_or_gh_projects_provenance(self):
        """The board fetch is gone: no project_status field, no gh_projects prov."""
        result = gather(
            jarvis_home="/fake",
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_labels_gh(
                [
                    {"number": 1, "title": "x", "labels": [], "updatedAt": "2024-05-01T00:00:00Z"},
                ]
            ),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )

        jarvis = result.repos[0]
        for issue in jarvis["issues"]:
            assert "project_status" not in issue
        assert not any(k.startswith("gh_projects") for k in result.provenance)

    def test_parse_repos_conf_ignores_project_token(self):
        # A stray `project=N` token in repos.conf must not leak into the repo
        # list; it is simply ignored now that nothing consumes it.
        content = "Osasuwu/jarvis project=3\nyour-username/your-second-repo project=1\n"
        assert parse_repos_conf(content) == [
            "Osasuwu/jarvis",
            "your-username/your-second-repo",
        ]


class TestJarvisHomeResolution:
    """Resolution precedence: explicit arg > $JARVIS_HOME > git rev-parse > cwd.

    Regression: gather() previously resolved only via `git rev-parse`, which
    keys off the *current* repo. A call from another repo (e.g. your-second-repo) then
    resolved to the wrong toplevel and degraded the whole gather. $JARVIS_HOME
    must pin the jarvis root before git rev-parse is ever consulted.
    """

    def _run(self, monkeypatch, jarvis_home):
        """Run gather with all I/O stubbed; guard git rev-parse from firing."""
        called = {"rev_parse": False}

        def _guard_subprocess_run(cmd, *a, **k):
            if isinstance(cmd, (list, tuple)) and "rev-parse" in cmd:
                called["rev_parse"] = True
            raise AssertionError(f"unexpected subprocess.run({cmd})")

        monkeypatch.setattr(status_gather.subprocess, "run", _guard_subprocess_run)
        gather(
            jarvis_home=jarvis_home,
            read_repos_conf_fn=_fixture_repos_conf("Osasuwu/jarvis\n"),
            read_device_json_fn=_fixture_device_json({"repos_path": "/fake/repos"}),
            run_git_fn=_fixture_run_git({"stdout": "main", "stderr": "", "returncode": 0}),
            run_gh_fn=_fixture_run_gh(_make_gh_success([])),
            query_supabase_fn=_fixture_query_supabase([]),
            now_fn=_fixture_now(),
        )
        return called

    def test_env_var_short_circuits_git_rev_parse(self, monkeypatch):
        """Empty arg + $JARVIS_HOME set → env wins, git rev-parse not called."""
        monkeypatch.setenv("JARVIS_HOME", "/env/jarvis")
        called = self._run(monkeypatch, jarvis_home="")
        assert called["rev_parse"] is False

    def test_explicit_arg_beats_env_var(self, monkeypatch):
        """Explicit jarvis_home wins over $JARVIS_HOME; git rev-parse untouched."""
        monkeypatch.setenv("JARVIS_HOME", "/env/jarvis")
        called = self._run(monkeypatch, jarvis_home="/explicit/jarvis")
        assert called["rev_parse"] is False
