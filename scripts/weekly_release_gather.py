"""I/O adapter for the `/weekly-release` capability (#1572).

Traverses config/repos.conf for repos opted into weekly releases (a
`releases=weekly` token), and for each one collects: prior GitHub releases
(trust-ramp input + last-release anchor), the merged-PR/closed-issue window,
and open milestone issues with movement in that window (Remaining-section
input for scripts/weekly_release_engine.py's assemble_release_body). The pure
decision core lives in weekly_release_engine.py (#1572); this module only
does gh/repos.conf I/O.

Deliberately built on the SAME injectable I/O callables as gather_common.py
(RunGhFn, QuerySupabaseFn, NowFn) and reuses its default gh/Supabase
implementations rather than introducing a parallel seam — those callables
were originally defined in status_gather.py and shared via that module
(#1586) until status_gather.py itself was retired (#1801), at which point
they moved to gather_common.py. repos.conf is read through a new
ReadReposConfEntriesFn seam because none of gather_common.py's existing
callables expose token data — parse_repos_conf is documented as "must never
change shape" (#1059) — and repos_conf.py's own docstring names the
weekly-release skill as the intended consumer of parse_repos_conf_entries.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.gather_common import (
    NowFn,
    Provenance,
    QuerySupabaseFn,
    RunGhFn,
    _default_run_gh,
    resolve_jarvis_home,
)
from scripts.repos_conf import REPOS_CONF_RELPATH, RepoEntry, parse_repos_conf_entries
from scripts.weekly_release_engine import compute_window

# ============================================================================
# Injectable I/O callables
# ============================================================================

# read_repos_conf_entries(path) -> list[RepoEntry] (structured, tokens kept)
ReadReposConfEntriesFn = Callable[[str], list[RepoEntry]]


def _default_read_repos_conf_entries(path: str) -> list[RepoEntry]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return []
    return parse_repos_conf_entries(raw)


# ============================================================================
# Canonical source identifiers
# ============================================================================


class WeeklyReleaseSourceKind:
    REPOS_CONF = "repos_conf"
    GH_RELEASES = "gh_releases"
    GH_MERGED_PRS = "gh_merged_prs"
    GH_CLOSED_ISSUES = "gh_closed_issues"
    GH_MILESTONE_ISSUES = "gh_milestone_issues"


# ============================================================================
# Data structures
# ============================================================================


@dataclass
class RepoWindowResult:
    """Everything gathered for one weekly-release repo."""

    repo: str
    lang: str = ""  # from repos.conf `lang=` token, "" if absent
    prior_releases: list[dict] = field(default_factory=list)  # most-recent-first
    window_entries: list[dict] = field(
        default_factory=list
    )  # merged PRs + standalone closed issues
    window_refs: set[str] = field(
        default_factory=set
    )  # PR/issue numbers as strings, for the linter
    remaining_issues: list[dict] = field(default_factory=list)  # open milestone issues w/ movement
    # remaining_issues' own numbers, as strings - a separate set from
    # window_refs (merged/closed only) because remaining_section prose cites
    # these open-issue numbers as its source of truth; lint_release_notes
    # must validate remaining_section against window_refs | remaining_refs,
    # never window_refs alone, or every remaining_section line fails as
    # "missing citation" by construction (found via e2e test against redrobot).
    remaining_refs: set[str] = field(default_factory=set)
    # #1659: merged PRs in the window that carry a "Reverts #N" marker, each
    # {"original_ref", "revert_ref", "title"} - source data for the
    # "Отозвано" release-notes section (format_retraction_section).
    retractions: list[dict] = field(default_factory=list)
    window_start: str = ""
    window_end: str = ""
    window_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "lang": self.lang,
            "prior_releases": self.prior_releases,
            "window_entries": self.window_entries,
            "window_refs": sorted(self.window_refs),
            "remaining_issues": self.remaining_issues,
            "remaining_refs": sorted(self.remaining_refs),
            "retractions": self.retractions,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window_truncated": self.window_truncated,
        }


@dataclass
class WeeklyReleaseGatherResult:
    repos: list[RepoWindowResult] = field(default_factory=list)
    provenance: dict[str, dict] = field(default_factory=dict)
    gathered_at: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repos": [r.to_dict() for r in self.repos],
            "provenance": dict(self.provenance),
            "gathered_at": self.gathered_at,
            "errors": self.errors,
        }


# ============================================================================
# Per-repo gather helpers
# ============================================================================

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.IGNORECASE)
# #1659: mirrors _CLOSES_RE - "Reverts #N" is the machine-readable retraction
# marker a revert PR/commit uses to name the PR/issue whose shipped behavior
# it undoes, so /weekly-release can build the "Отозвано" section by construction.
_REVERTS_RE = re.compile(r"\breverts?\s*:?\s*#(\d+)", re.IGNORECASE)


def _closed_issue_number(pr_body: str) -> str | None:
    m = _CLOSES_RE.search(pr_body)
    return m.group(1) if m else None


def _reverted_pr_number(pr_body: str) -> str | None:
    m = _REVERTS_RE.search(pr_body)
    return m.group(1) if m else None


def _gather_prior_releases(repo: str, run_gh: RunGhFn, now: float) -> tuple[list[dict], Provenance]:
    """Release history, most-recent-first — the shape trust_ramp_state()
    expects, and the source of compute_window()'s last_release_at anchor."""
    start = time.time()
    result = run_gh(
        repo,
        [
            "api",
            f"repos/{repo}/releases",
            "--paginate",
            "-q",
            ".[] | {tag_name, published_at, created_at, draft}",
        ],
    )
    ok = result["returncode"] == 0
    elapsed = time.time() - start
    prov = Provenance(ran=True, ok=ok, input_rows=-1, age=elapsed)
    if not ok or not result["stdout"]:
        return [], prov

    releases: list[dict] = []
    for line in result["stdout"].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        releases.append(
            {
                "tag_name": row.get("tag_name"),
                "published_at": row.get("published_at"),
                "created_at": row.get("created_at"),
                "published": not row.get("draft", False),
                # ceiling: the GitHub releases API has no "edited after
                # publish" signal short of comparing published_at against a
                # stored last-edit timestamp, which needs local state - and
                # #1572 explicitly rules local trust-ramp state out in favor
                # of a live read. Defaults to False; trust_ramp_state() still
                # gates on `published` alone until a real signal exists.
                "edited_after_publish": False,
            }
        )
    prov.input_rows = len(releases)
    return releases, prov


def _gather_merged_window(
    repo: str, run_gh: RunGhFn, start_iso: str, end_iso: str
) -> tuple[list[dict], set[str], Provenance]:
    """Merged PRs in [start_iso, end_iso), flagging Dependabot authorship and
    extracting a linked `Closes #NNN` issue number for cross-collection dedup.

    ceiling: single un-paginated `gh pr list --limit 100`; a repo merging
    more than 100 PRs in one week would silently truncate. Extend with
    --paginate (mirroring _gather_prior_releases) if a tracked repo ever
    approaches that volume.
    """
    start = time.time()
    result = run_gh(
        repo,
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            "100",
            "--json",
            "number,title,mergedAt,author,body",
        ],
    )
    ok = result["returncode"] == 0
    elapsed = time.time() - start
    prov = Provenance(ran=True, ok=ok, input_rows=-1, age=elapsed)
    if not ok or not result["stdout"]:
        return [], set(), prov

    try:
        prs = json.loads(result["stdout"])
    except json.JSONDecodeError:
        prov.ok = False
        return [], set(), prov

    entries: list[dict] = []
    refs: set[str] = set()
    for pr in prs:
        merged_at = pr.get("mergedAt") or ""
        if not (start_iso <= merged_at < end_iso):
            continue
        author = pr.get("author") or {}
        is_dependabot = (author.get("login") or "").lower() in {"dependabot", "dependabot[bot]"}
        number = str(pr["number"])
        refs.add(number)
        closed_issue = _closed_issue_number(pr.get("body") or "")
        if closed_issue:
            refs.add(closed_issue)
        reverts = _reverted_pr_number(pr.get("body") or "")
        if reverts:
            refs.add(reverts)
        entries.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "merged_at": merged_at,
                "is_dependabot": is_dependabot,
                "closes_issue": closed_issue,
                "reverts": reverts,
            }
        )
    prov.input_rows = len(entries)
    return entries, refs, prov


def _gather_closed_issues_window(
    repo: str,
    run_gh: RunGhFn,
    start_iso: str,
    end_iso: str,
    pr_linked_issue_refs: set[str],
) -> tuple[list[dict], set[str], Provenance]:
    """Closed issues in [start_iso, end_iso) not already represented by a
    merged PR in the same window (`pr_linked_issue_refs`), excluding issues
    closed as not_planned — declined work isn't release content.

    ceiling: single un-paginated `gh issue list --limit 100`; a repo closing
    more than 100 issues in one week would silently truncate. Extend with
    --paginate (mirroring _gather_prior_releases) if a tracked repo ever
    approaches that volume.
    """
    start = time.time()
    result = run_gh(
        repo,
        [
            "issue",
            "list",
            "--state",
            "closed",
            "--limit",
            "100",
            "--json",
            "number,title,closedAt,stateReason",
        ],
    )
    ok = result["returncode"] == 0
    elapsed = time.time() - start
    prov = Provenance(ran=True, ok=ok, input_rows=-1, age=elapsed)
    if not ok or not result["stdout"]:
        return [], set(), prov

    try:
        issues = json.loads(result["stdout"])
    except json.JSONDecodeError:
        prov.ok = False
        return [], set(), prov

    entries: list[dict] = []
    refs: set[str] = set()
    for issue in issues:
        if issue.get("stateReason") == "not_planned":
            continue
        closed_at = issue.get("closedAt") or ""
        if not (start_iso <= closed_at < end_iso):
            continue
        number = str(issue["number"])
        if number in pr_linked_issue_refs:
            continue
        refs.add(number)
        entries.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "closed_at": closed_at,
                "is_dependabot": False,
            }
        )
    prov.input_rows = len(entries)
    return entries, refs, prov


def _gather_remaining_issues(
    repo: str, run_gh: RunGhFn, start_iso: str, end_iso: str
) -> tuple[list[dict], Provenance]:
    """Open issues attached to a milestone with activity inside the window -
    feeds the release body's always-present Remaining/"Осталось" section.

    ceiling: "movement" is approximated as updatedAt falling inside the
    window, since `gh issue list` doesn't expose a timeline diff cheaply; an
    issue touched for an unrelated reason (e.g. relabeled) would count. This
    is drafted into prose by the calling agent, not rendered verbatim, so a
    false positive here is a minor drafting nuisance, not a linter failure.

    ceiling: also a single un-paginated `gh issue list --limit 100`; a repo
    with more than 100 open milestone-attached issues touched in one week
    would silently truncate. Extend with --paginate (mirroring
    _gather_prior_releases) if a tracked repo ever approaches that volume.
    """
    start = time.time()
    result = run_gh(
        repo,
        [
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,updatedAt,milestone",
        ],
    )
    ok = result["returncode"] == 0
    elapsed = time.time() - start
    prov = Provenance(ran=True, ok=ok, input_rows=-1, age=elapsed)
    if not ok or not result["stdout"]:
        return [], prov

    try:
        issues = json.loads(result["stdout"])
    except json.JSONDecodeError:
        prov.ok = False
        return [], prov

    remaining: list[dict] = []
    for issue in issues:
        if not issue.get("milestone"):
            continue
        updated = issue.get("updatedAt") or ""
        if not (start_iso <= updated < end_iso):
            continue
        remaining.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "milestone": (issue.get("milestone") or {}).get("title"),
                "updated_at": updated,
            }
        )
    prov.input_rows = len(remaining)
    return remaining, prov


# ============================================================================
# Main gather orchestrator
# ============================================================================


def gather(
    jarvis_home: str = "",
    *,
    now: str = "",
    # Injectable I/O callbacks (defaults = real implementations)
    read_repos_conf_entries_fn: ReadReposConfEntriesFn | None = None,
    run_gh_fn: RunGhFn | None = None,
    query_supabase_fn: QuerySupabaseFn | None = None,
    now_fn: NowFn | None = None,
) -> WeeklyReleaseGatherResult:
    """Gather per-repo weekly-release state for every repos.conf entry
    carrying a `releases=weekly` token.

    Args:
        jarvis_home: Root path of the jarvis repo. If empty, resolve from
                     $JARVIS_HOME, then `git rev-parse --show-toplevel`,
                     then CWD (same precedence as status_gather.gather()).
        now: ISO-8601 UTC anchor for compute_window()'s `now` input; empty
             defaults to now_fn()'s current time. Kept as an explicit string
             so callers driving compute_window() off the same instant this
             gather ran don't have to reconvert a float.
        read_repos_conf_entries_fn: Callable to read repos.conf with tokens
                                     (default: file I/O + parse_repos_conf_entries).
        run_gh_fn: Callable to run repo-scoped gh commands (default: subprocess).
        query_supabase_fn: Callable for Supabase REST queries (default: httpx).
                            Unused by this slice's gather logic but accepted
                            for seam symmetry with status_gather/morning_gather.
        now_fn: Callable returning epoch seconds (default: time.time).

    Returns:
        WeeklyReleaseGatherResult with per-repo release/window/remaining data.
    """
    _read_entries = read_repos_conf_entries_fn or _default_read_repos_conf_entries
    _run_gh = run_gh_fn or _default_run_gh
    _now_fn = now_fn or time.time
    del query_supabase_fn  # seam symmetry only, not used by this slice

    gather_start = _now_fn()
    gathered_at = datetime.fromtimestamp(gather_start, tz=timezone.utc).isoformat()
    now_iso = now or gathered_at

    result = WeeklyReleaseGatherResult(gathered_at=gathered_at)

    jarvis_home = resolve_jarvis_home(jarvis_home)

    conf_path = str(Path(jarvis_home) / REPOS_CONF_RELPATH)
    try:
        entries = _read_entries(conf_path)
    except ValueError as exc:
        # #1671: a parse error must not be masked as ok=True/input_rows=0 —
        # that reads as "repos.conf legitimately has zero weekly repos"
        # instead of "repos.conf is broken and nothing was gathered".
        result.errors.append(f"repos.conf parse error: {exc}")
        result.provenance[WeeklyReleaseSourceKind.REPOS_CONF] = Provenance(
            ran=True, ok=False, input_rows=0, age=0.0
        ).to_dict()
        return result

    if not entries:
        # #1671: mirrors status_gather.py's empty/unreadable handling —
        # distinct from "parsed fine, zero releases=weekly rows" below.
        result.errors.append("repos.conf is empty, unreadable, or not found")
        result.provenance[WeeklyReleaseSourceKind.REPOS_CONF] = Provenance(
            ran=True, ok=False, input_rows=0, age=0.0
        ).to_dict()
        return result

    weekly_entries = [e for e in entries if e.tokens.get("releases") == "weekly"]
    result.provenance[WeeklyReleaseSourceKind.REPOS_CONF] = Provenance(
        ran=True, ok=True, input_rows=len(weekly_entries), age=0.0
    ).to_dict()

    for entry in weekly_entries:
        repo = entry.name
        releases, releases_prov = _gather_prior_releases(repo, _run_gh, gather_start)

        last_release_at = next(
            (r["published_at"] for r in releases if r.get("published") and r.get("published_at")),
            None,
        )
        # #1667: compute_window() anchors on last_release_at only — a pending
        # (unpublished) draft never anchors the window. SKILL.md Step 2.6
        # separately handles the draft-in-place-update case by scanning
        # prior_releases, so compute_window() doesn't need draft awareness.
        window = compute_window(last_release_at, now_iso)

        merged, merged_refs, merged_prov = _gather_merged_window(
            repo, _run_gh, window.start, window.end
        )
        closed_issues, closed_refs, closed_prov = _gather_closed_issues_window(
            repo,
            _run_gh,
            window.start,
            window.end,
            {e["closes_issue"] for e in merged if e.get("closes_issue")},
        )
        remaining, remaining_prov = _gather_remaining_issues(
            repo, _run_gh, window.start, window.end
        )
        retractions = [
            {
                "original_ref": e["reverts"],
                "revert_ref": str(e["number"]),
                "title": e["title"],
            }
            for e in merged
            if e.get("reverts")
        ]

        repo_result = RepoWindowResult(
            repo=repo,
            lang=entry.tokens.get("lang", ""),
            prior_releases=releases,
            window_entries=merged + closed_issues,
            window_refs=merged_refs | closed_refs,
            remaining_issues=remaining,
            remaining_refs={str(i["number"]) for i in remaining},
            retractions=retractions,
            window_start=window.start,
            window_end=window.end,
            window_truncated=window.truncated,
        )
        result.repos.append(repo_result)
        result.provenance[f"{repo}:{WeeklyReleaseSourceKind.GH_RELEASES}"] = releases_prov.to_dict()
        result.provenance[f"{repo}:{WeeklyReleaseSourceKind.GH_MERGED_PRS}"] = merged_prov.to_dict()
        result.provenance[f"{repo}:{WeeklyReleaseSourceKind.GH_CLOSED_ISSUES}"] = (
            closed_prov.to_dict()
        )
        result.provenance[f"{repo}:{WeeklyReleaseSourceKind.GH_MILESTONE_ISSUES}"] = (
            remaining_prov.to_dict()
        )

    return result


def main(argv: list[str] | None = None) -> int:
    result = gather()
    json.dump(result.to_dict(), sys.stdout, indent=2, default=str)
    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
