"""#1572 - weekly_release_gather.py: repos.conf traversal (releases=weekly
filter), merged-PR window collection (dedup vs closed issues, Dependabot
filter, UTC window boundaries), closed-issue window (not_planned exclusion),
and remaining-issue collection (milestone-scoped, movement-in-window).

All gh/repos.conf I/O is injected via fakes - no real subprocess/network calls.
"""

from __future__ import annotations

import json

from scripts.repos_conf import RepoEntry
from scripts.weekly_release_gather import (
    WeeklyReleaseSourceKind,
    _gather_closed_issues_window,
    _gather_merged_window,
    _gather_prior_releases,
    _gather_remaining_issues,
    gather,
)

WINDOW_START = "2026-08-01T00:00:00+00:00"
WINDOW_END = "2026-08-20T00:00:00+00:00"


def _gh(stdout: str = "", returncode: int = 0):
    def fn(repo, args):
        return {"stdout": stdout, "stderr": "", "returncode": returncode}

    return fn


# -- _gather_prior_releases ----------------------------------------------------


def test_prior_releases_parses_release_rows():
    ndjson = "\n".join(
        json.dumps(r)
        for r in [
            {"tag_name": "v0.2.0", "published_at": "2026-08-01T00:00:00Z", "draft": False},
            {"tag_name": "v0.1.0", "published_at": "2026-07-01T00:00:00Z", "draft": False},
        ]
    )
    releases, prov = _gather_prior_releases("o/r", _gh(ndjson), now=0.0)
    assert [r["tag_name"] for r in releases] == ["v0.2.0", "v0.1.0"]
    assert prov.ok is True
    assert prov.input_rows == 2


def test_prior_releases_empty_on_gh_failure():
    releases, prov = _gather_prior_releases("o/r", _gh("", returncode=1), now=0.0)
    assert releases == []
    assert prov.ok is False


# -- _gather_merged_window ------------------------------------------------------


def test_merged_window_filters_by_merged_at():
    prs = [
        {
            "number": 10,
            "title": "feat: add thing",
            "mergedAt": "2026-08-10T00:00:00Z",
            "author": {"login": "alice"},
            "body": "",
        },
        {
            "number": 11,
            "title": "chore: old",
            "mergedAt": "2026-07-01T00:00:00Z",
            "author": {"login": "alice"},
            "body": "",
        },
    ]
    entries, refs, prov = _gather_merged_window(
        "o/r", _gh(json.dumps(prs)), WINDOW_START, WINDOW_END
    )
    assert [e["number"] for e in entries] == [10]
    assert refs == {"10"}


def test_merged_window_flags_dependabot():
    prs = [
        {
            "number": 12,
            "title": "chore(deps): bump x",
            "mergedAt": "2026-08-10T00:00:00Z",
            "author": {"login": "dependabot[bot]"},
            "body": "",
        },
    ]
    entries, refs, prov = _gather_merged_window(
        "o/r", _gh(json.dumps(prs)), WINDOW_START, WINDOW_END
    )
    assert entries[0]["is_dependabot"] is True


def test_merged_window_extracts_closed_issue_for_dedup():
    prs = [
        {
            "number": 13,
            "title": "fix: bug",
            "mergedAt": "2026-08-10T00:00:00Z",
            "author": {"login": "alice"},
            "body": "Closes #999",
        },
    ]
    entries, refs, prov = _gather_merged_window(
        "o/r", _gh(json.dumps(prs)), WINDOW_START, WINDOW_END
    )
    assert entries[0]["closes_issue"] == "999"
    assert refs == {"13", "999"}


def test_merged_window_extracts_reverts_marker():
    prs = [
        {
            "number": 14,
            "title": "revert: bad rollout behavior",
            "mergedAt": "2026-08-10T00:00:00Z",
            "author": {"login": "alice"},
            "body": "Reverts #1000",
        },
    ]
    entries, refs, prov = _gather_merged_window(
        "o/r", _gh(json.dumps(prs)), WINDOW_START, WINDOW_END
    )
    assert entries[0]["reverts"] == "1000"
    assert refs == {"14", "1000"}


def test_merged_window_reverts_is_none_when_absent():
    prs = [
        {
            "number": 15,
            "title": "feat: add thing",
            "mergedAt": "2026-08-10T00:00:00Z",
            "author": {"login": "alice"},
            "body": "",
        },
    ]
    entries, refs, prov = _gather_merged_window(
        "o/r", _gh(json.dumps(prs)), WINDOW_START, WINDOW_END
    )
    assert entries[0]["reverts"] is None


# -- _gather_closed_issues_window -----------------------------------------------


def test_closed_issues_excludes_not_planned():
    issues = [
        {
            "number": 20,
            "title": "wontfix",
            "closedAt": "2026-08-10T00:00:00Z",
            "stateReason": "not_planned",
        },
        {
            "number": 21,
            "title": "real fix",
            "closedAt": "2026-08-11T00:00:00Z",
            "stateReason": "completed",
        },
    ]
    entries, refs, prov = _gather_closed_issues_window(
        "o/r", _gh(json.dumps(issues)), WINDOW_START, WINDOW_END, pr_linked_issue_refs=set()
    )
    assert [e["number"] for e in entries] == [21]


def test_closed_issues_dedups_against_pr_linked_issues():
    issues = [
        {
            "number": 22,
            "title": "already covered by its PR",
            "closedAt": "2026-08-11T00:00:00Z",
            "stateReason": "completed",
        },
    ]
    entries, refs, prov = _gather_closed_issues_window(
        "o/r", _gh(json.dumps(issues)), WINDOW_START, WINDOW_END, pr_linked_issue_refs={"22"}
    )
    assert entries == []


# -- _gather_remaining_issues ----------------------------------------------------


def test_remaining_issues_requires_milestone_and_movement_in_window():
    issues = [
        {
            "number": 30,
            "title": "in milestone, moved",
            "updatedAt": "2026-08-10T00:00:00Z",
            "milestone": {"title": "M1"},
        },
        {
            "number": 31,
            "title": "no milestone",
            "updatedAt": "2026-08-10T00:00:00Z",
            "milestone": None,
        },
        {
            "number": 32,
            "title": "stale",
            "updatedAt": "2026-01-01T00:00:00Z",
            "milestone": {"title": "M1"},
        },
    ]
    remaining, prov = _gather_remaining_issues(
        "o/r", _gh(json.dumps(issues)), WINDOW_START, WINDOW_END
    )
    assert [r["number"] for r in remaining] == [30]


# -- gather() end-to-end ---------------------------------------------------------


def test_gather_populates_remaining_refs_from_remaining_issues():
    # remaining_refs mirrors window_refs but for open milestone issues -
    # remaining_section prose cites these, and they are categorically
    # disjoint from window_refs (merged/closed only). Missing this field
    # made every non-empty remaining_section fail lint_release_notes by
    # construction (found via e2e test against redrobot).
    entries = [RepoEntry(name="o/weekly-repo", tokens={"releases": "weekly"})]

    open_issues_json = json.dumps(
        [
            {
                "number": 77,
                "title": "still open",
                "updatedAt": "2026-08-10T00:00:00Z",
                "milestone": {"title": "M1"},
            }
        ]
    )

    def fake_run_gh(repo, args):
        if args[0] == "api":
            return {"stdout": "", "stderr": "", "returncode": 0}
        if args[0] == "issue":
            return {"stdout": open_issues_json, "stderr": "", "returncode": 0}
        return {"stdout": "[]", "stderr": "", "returncode": 0}

    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: entries,
        run_gh_fn=fake_run_gh,
        now_fn=lambda: 1786000000.0,
    )
    repo_result = result.repos[0]
    assert repo_result.remaining_refs == {"77"}
    assert "77" not in repo_result.window_refs


def test_gather_filters_repos_conf_to_weekly_releases_only():
    entries = [
        RepoEntry(name="o/weekly-repo", tokens={"releases": "weekly"}),
        RepoEntry(name="o/other-repo", tokens={}),
    ]

    def fake_run_gh(repo, args):
        if args[0] == "api":
            return {"stdout": "", "stderr": "", "returncode": 0}
        return {"stdout": "[]", "stderr": "", "returncode": 0}

    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: entries,
        run_gh_fn=fake_run_gh,
        now_fn=lambda: 1786000000.0,
    )
    repo_names = [r.repo for r in result.repos]
    assert repo_names == ["o/weekly-repo"]
    assert WeeklyReleaseSourceKind.REPOS_CONF in result.provenance


def test_gather_anchors_window_on_last_published_release_ignoring_pending_draft():
    # #1667: a repo's most-recent release is an unpublished (pending) draft.
    # The window must anchor on the last *published* release, not on the
    # draft's created_at - anchoring on the draft collapsed the window to
    # near-empty on every re-run (#1667). SKILL.md Step 2.6 handles updating
    # an in-place draft separately, by scanning prior_releases.
    entries = [RepoEntry(name="o/weekly-repo", tokens={"releases": "weekly"})]

    releases_ndjson = "\n".join(
        json.dumps(r)
        for r in [
            {
                "tag_name": "v0.3.0",
                "published_at": None,
                "created_at": "2026-08-15T00:00:00Z",
                "draft": True,
            },
            {
                "tag_name": "v0.2.0",
                "published_at": "2026-08-01T00:00:00Z",
                "created_at": "2026-08-01T00:00:00Z",
                "draft": False,
            },
        ]
    )

    def fake_run_gh(repo, args):
        if args[0] == "api":
            return {"stdout": releases_ndjson, "stderr": "", "returncode": 0}
        return {"stdout": "[]", "stderr": "", "returncode": 0}

    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: entries,
        run_gh_fn=fake_run_gh,
        now_fn=lambda: 1786000000.0,
    )
    repo_result = result.repos[0]
    assert repo_result.window_start == "2026-08-01T00:00:00+00:00"


def test_gather_second_run_with_pending_draft_still_yields_nonempty_window():
    # #1667 AC4 runnable check: two consecutive gather() runs on the same
    # window, with an existing pending (unpublished) draft already in
    # prior_releases, must each produce a non-empty window - not just the
    # first. Pre-fix, anchoring on the draft's created_at (more recent than
    # the last publish) collapsed the window to near-empty on re-run, which
    # is exactly the state SKILL.md Step 2.6 needs a non-empty window to
    # update the same draft in place rather than silently going quiet.
    entries = [RepoEntry(name="o/weekly-repo", tokens={"releases": "weekly"})]

    releases_ndjson = "\n".join(
        json.dumps(r)
        for r in [
            {
                "tag_name": "v0.3.0",
                "published_at": None,
                "created_at": "2026-08-15T00:00:00Z",
                "draft": True,
            },
            {
                "tag_name": "v0.2.0",
                "published_at": "2026-08-01T00:00:00Z",
                "created_at": "2026-08-01T00:00:00Z",
                "draft": False,
            },
        ]
    )
    merged_prs_json = json.dumps(
        [
            {
                "number": 42,
                "title": "feat: ship thing",
                "mergedAt": "2026-08-10T00:00:00Z",
                "author": {"login": "alice"},
                "body": "",
            }
        ]
    )

    def fake_run_gh(repo, args):
        if args[0] == "api":
            return {"stdout": releases_ndjson, "stderr": "", "returncode": 0}
        if args[0] == "pr":
            return {"stdout": merged_prs_json, "stderr": "", "returncode": 0}
        return {"stdout": "[]", "stderr": "", "returncode": 0}

    def run_once():
        result = gather(
            jarvis_home="/fake",
            now="2026-08-20T00:00:00+00:00",
            read_repos_conf_entries_fn=lambda path: entries,
            run_gh_fn=fake_run_gh,
            now_fn=lambda: 1786000000.0,
        )
        return result.repos[0]

    first_run = run_once()
    second_run = run_once()

    for repo_result in (first_run, second_run):
        assert repo_result.window_start == "2026-08-01T00:00:00+00:00"
        assert repo_result.window_entries != []
        assert repo_result.window_refs == {"42"}

    # Same fixture in -> same window both times: the second run does not
    # collapse relative to the first (the #1667 regression).
    assert second_run.window_start == first_run.window_start
    assert second_run.window_entries == first_run.window_entries
    assert second_run.window_refs == first_run.window_refs


# -- gather() repos.conf failure handling (#1671) ---------------------------


def test_gather_reports_error_on_repos_conf_parse_error():
    def raising_read(path):
        raise ValueError("bad token on line 3")

    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=raising_read,
        run_gh_fn=_gh("[]"),
        now_fn=lambda: 1786000000.0,
    )
    assert result.repos == []
    assert any("repos.conf parse error" in e for e in result.errors)
    assert result.provenance[WeeklyReleaseSourceKind.REPOS_CONF]["ok"] is False


def test_gather_reports_error_on_empty_repos_conf():
    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: [],
        run_gh_fn=_gh("[]"),
        now_fn=lambda: 1786000000.0,
    )
    assert result.repos == []
    assert any("repos.conf is empty, unreadable, or not found" in e for e in result.errors)
    assert result.provenance[WeeklyReleaseSourceKind.REPOS_CONF]["ok"] is False


def test_gather_reports_ok_when_repos_conf_has_no_weekly_entries():
    # Distinct from the empty-file case: parsing succeeded, there just
    # aren't any releases=weekly rows - not a failure.
    entries = [RepoEntry(name="o/other-repo", tokens={})]
    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: entries,
        run_gh_fn=_gh("[]"),
        now_fn=lambda: 1786000000.0,
    )
    assert result.repos == []
    assert result.errors == []
    assert result.provenance[WeeklyReleaseSourceKind.REPOS_CONF]["ok"] is True
    assert result.provenance[WeeklyReleaseSourceKind.REPOS_CONF]["input_rows"] == 0


def test_gather_collects_retractions_from_merged_window():
    # #1659 AC1/AC3: a merged PR whose body carries "Reverts #N" must surface
    # in repo_result.retractions, citing both the reverting PR and the
    # original PR/issue it undoes - the source data for the "Отозвано" section.
    entries = [RepoEntry(name="o/weekly-repo", tokens={"releases": "weekly"})]

    prs_ndjson = json.dumps(
        [
            {
                "number": 50,
                "title": "revert: undo risky rollout",
                "mergedAt": "2026-08-10T00:00:00Z",
                "author": {"login": "alice"},
                "body": "Reverts #40",
            }
        ]
    )

    def fake_run_gh(repo, args):
        if args[0] == "api":
            return {"stdout": "", "stderr": "", "returncode": 0}
        if args[0] == "pr":
            return {"stdout": prs_ndjson, "stderr": "", "returncode": 0}
        return {"stdout": "[]", "stderr": "", "returncode": 0}

    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: entries,
        run_gh_fn=fake_run_gh,
        now_fn=lambda: 1786000000.0,
    )
    repo_result = result.repos[0]
    assert repo_result.retractions == [
        {"original_ref": "40", "revert_ref": "50", "title": "revert: undo risky rollout"}
    ]


def test_gather_retractions_empty_when_no_reverts_in_window():
    entries = [RepoEntry(name="o/weekly-repo", tokens={"releases": "weekly"})]

    def fake_run_gh(repo, args):
        if args[0] == "api":
            return {"stdout": "", "stderr": "", "returncode": 0}
        return {"stdout": "[]", "stderr": "", "returncode": 0}

    result = gather(
        jarvis_home="/fake",
        now="2026-08-20T00:00:00+00:00",
        read_repos_conf_entries_fn=lambda path: entries,
        run_gh_fn=fake_run_gh,
        now_fn=lambda: 1786000000.0,
    )
    assert result.repos[0].retractions == []
