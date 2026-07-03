"""Tiny GitHub Events API client for the monitor agent.

Scope (Sprint 1, issue #174): fetch recent repo events for classification.
Nothing else — no issue creation, no PR management. The agent observes;
it doesn't act.

Uses ``httpx``, declared as a direct dependency of the ``[agents]`` extra
in ``pyproject.toml``. (It was originally picked up transitively via
``supabase-py`` — see #183 for why the contract was tightened.)

PR-evidence checking for issue #953: deterministic checks for PR existence
and activity to decide whether a task produced actionable work.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

# Events we care about for classification. Everything else (WatchEvent,
# ForkEvent, CreateEvent of a branch…) is pure noise for the Sprint 1
# proof of concept. Keeping the allow-list tight avoids paying Ollama
# tokens on events we'd immediately drop anyway.
RELEVANT_EVENT_TYPES = frozenset(
    {
        "IssuesEvent",
        "PullRequestEvent",
        "PullRequestReviewEvent",
        "IssueCommentEvent",
        "PullRequestReviewCommentEvent",
        "PushEvent",
    }
)


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# GitHub's /events endpoint serves at most ~300 events total across up to
# 3 pages. ``per_page=100`` is the maximum the API accepts.
_MAX_EVENTS_PAGES = 3
_PER_PAGE = 100


def fetch_repo_events(
    repo: str,
    *,
    after_event_id: str | None = None,
    limit: int = 10,
    token: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Return recent events for ``repo`` filtered to RELEVANT_EVENT_TYPES,
    oldest first.

    GitHub returns events newest-first; this function paginates (up to 3
    pages of 100), filters to the allow-list, and re-sorts ascending before
    slicing to ``limit``. Taking the oldest N makes the monitor's cursor
    advance contiguous: the next poll resumes right after the last stored
    event rather than skipping past older-but-still-new activity that didn't
    fit in the slice.

    * ``after_event_id`` — drop any event whose id is <= this one. GitHub
      event ids are monotonically increasing integer-strings; this function
      converts them with ``int(...)`` and compares numerically.
    * ``limit`` — cap how many matching events to return per call (and
      therefore how many Ollama classifications are paid for). Default 10.
    * ``token`` — optional GitHub token. Unauthenticated requests hit a
      60 req/hour rate limit per IP, which is fine for low-frequency
      monitoring but will trip on busy repos.

    Pagination stops early when a page's oldest event id is <= the cursor
    (nothing newer can exist on later pages) or when a short page signals
    the end of available history. This prevents silent event loss on busy
    repos where more than one page of relevant activity lands between polls.
    """
    auth_token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    cursor = int(after_event_id) if after_event_id else 0
    url = f"https://api.github.com/repos/{repo}/events"

    collected: list[dict[str, Any]] = []
    for page in range(1, _MAX_EVENTS_PAGES + 1):
        response = httpx.get(
            url,
            headers=_headers(auth_token),
            params={"per_page": _PER_PAGE, "page": page},
            timeout=timeout,
        )
        response.raise_for_status()
        page_events: list[dict[str, Any]] = response.json()
        if not page_events:
            break

        for event in page_events:
            if event.get("type") in RELEVANT_EVENT_TYPES and int(event.get("id", "0")) > cursor:
                collected.append(event)

        # The last element on each page is the oldest on that page (GitHub
        # returns newest-first). If it's already <= cursor, every event on
        # subsequent pages is strictly older — no further matches possible.
        oldest_on_page = int(page_events[-1].get("id", "0"))
        if oldest_on_page <= cursor:
            break
        # A partial page means GitHub has no more history to return.
        if len(page_events) < _PER_PAGE:
            break

    collected.sort(key=lambda e: int(e.get("id", "0")))
    return collected[:limit]


def summarise_event(event: dict[str, Any]) -> str:
    """Return a one-line summary suitable for LLM classification.

    Pulls only the fields the model actually needs — keeps prompts small
    and removes the URL soup GitHub ships in each event payload.
    """
    event_type = event.get("type", "UnknownEvent")
    actor = event.get("actor", {}).get("login", "unknown")
    repo_name = event.get("repo", {}).get("name", "unknown")
    payload = event.get("payload", {}) or {}

    # Type-specific extraction — each branch grabs the one or two human
    # fields that make the event interpretable.
    if event_type == "IssuesEvent":
        action = payload.get("action", "?")
        issue = payload.get("issue", {}) or {}
        detail = f"{action} #{issue.get('number')}: {issue.get('title', '')}"
    elif event_type == "PullRequestEvent":
        action = payload.get("action", "?")
        pr = payload.get("pull_request", {}) or {}
        detail = f"{action} PR #{pr.get('number')}: {pr.get('title', '')}"
    elif event_type == "PullRequestReviewEvent":
        review = payload.get("review", {}) or {}
        pr = payload.get("pull_request", {}) or {}
        detail = f"review ({review.get('state', '?')}) on PR #{pr.get('number')}"
    elif event_type in ("IssueCommentEvent", "PullRequestReviewCommentEvent"):
        action = payload.get("action", "created")
        issue = payload.get("issue", payload.get("pull_request", {})) or {}
        detail = f"comment {action} on #{issue.get('number')}"
    elif event_type == "PushEvent":
        ref = payload.get("ref", "?")
        commits = len(payload.get("commits", []) or [])
        detail = f"push {commits} commits to {ref}"
    else:
        detail = "(details omitted)"

    return f"[{event_type}] {repo_name} by {actor}: {detail}"


# =============================================================================
# PR-Evidence Checking for Issue #953 — Event-Driven Task Completion
# =============================================================================


# A rework goal directly names the PR to continue (e.g. "/rework #42"). The
# pattern is unanchored on purpose: a re-driven goal is prefixed ("Re-drive
# (attempt 2): /rework #42"), so anchoring to the start would misclassify it
# as fresh-shape and break the AC5 no-augmentation rule.
_REWORK_GOAL_RE = re.compile(r"(?i)/rework\s+#?(\d+)")


def parse_goal_shape(goal: str) -> tuple[str, int | None]:
    """Classify a task goal into its evidence shape (AC2 #953).

    Returns ``(shape, pr_number)``:

    - ``("empty", None)`` — blank/whitespace goal; no evidence can be computed.
    - ``("rework", N)`` — goal references PR #N via ``/rework #N``; evidence is
      *new activity on PR #N* since spawn.
    - ``("fresh", None)`` — anything else; evidence is *a PR exists* on the
      task's working branch (``task/<task_id>`` or an explicit ``(branch=...)``).
    """
    if not goal or not goal.strip():
        return ("empty", None)
    m = _REWORK_GOAL_RE.search(goal)
    if m:
        return ("rework", int(m.group(1)))
    return ("fresh", None)


class GitHubClient(Protocol):
    """Protocol for GitHub API calls needed by PR-evidence checks (AC4 #953).

    Used for mocking in tests; real implementation injected from orchestrator.
    """

    def get_pull_by_head_branch(self, branch: str) -> dict[str, Any] | None:
        """Fetch PR by head branch name; returns None if not found."""

    def get_pull_by_number(self, pr_number: int) -> dict[str, Any] | None:
        """Fetch PR by number; returns None if not found."""

    def list_commits_for_pull(self, pr_number: int) -> list[dict[str, Any]]:
        """List commits for a PR; returns empty list if not found."""


def parse_executor_stdout(stdout_text: str) -> dict[str, Any] | None:
    """Parse executor stdout JSON and extract PR number if present (AC3 #953).

    Executor spawned with ``--output-format json`` writes to
    ``logs/executor/<task_id>.stdout.json``. If the agent claimed a PR URL,
    extract and return {number: <pr_number>}; otherwise None.

    Handles malformed JSON gracefully — returns None rather than raising.
    """
    if not stdout_text:
        return None
    try:
        data = json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError):
        logger.debug("parse_executor_stdout: malformed JSON in stdout")
        return None

    # Top-level JSON must be an object — a list/scalar has no fields to scan
    # and ``data.get`` would AttributeError into the poll loop. Real Claude CLI
    # output is always an object; anything else degrades to the None sentinel.
    if not isinstance(data, dict):
        return None

    # Direct fields first — for structured / hand-written agent payloads.
    for field in ("pr_url", "pull_request_url", "url"):
        url = data.get(field)
        if url and isinstance(url, str):
            # Extract PR number from URL like https://github.com/owner/repo/pull/999
            match = re.search(r"/pull/(\d+)", url)
            if match:
                return {"number": int(match.group(1))}

    # ``claude --output-format json`` envelope (the shape ``spawn()`` actually
    # produces when a task_id is set): the agent's free-text answer — including
    # any PR URL — lives in the ``result`` string, not a top-level field. Scan
    # it for a /pull/<n> link. Without this the AC3 secondary channel is dead in
    # production while flat-JSON fixtures keep CI green (MAJOR, PR #1011).
    result_text = data.get("result")
    if isinstance(result_text, str):
        match = re.search(r"/pull/(\d+)", result_text)
        if match:
            return {"number": int(match.group(1))}

    return None


def check_pr_evidence_fresh_shape(
    task_id: str,
    goal: str,
    spawned_at: datetime,
    *,
    client: GitHubClient | None = None,
) -> bool | None:
    """Check PR evidence for fresh-shape goals (AC2 #953).

    Fresh-shape goals use the convention: create your working branch as
    `task/<task_id>` unless an explicit directive like (branch=...) appears.

    Evidence requires the PR to be *fresh* — created after the task spawned.
    Without the ``spawned_at`` gate a stale pre-existing PR reusing the branch
    name (or a left-over PR from a prior attempt) would falsely read as evidence
    that *this* spawn produced work (MAJOR, PR #1011).

    Returns:
    - True: PR exists on the branch AND was created after ``spawned_at``
    - False: No PR found, or the only PR predates ``spawned_at``
    - None: Unparseable goal (degenerate case; treated as escalate)
    """
    if client is None:
        logger.warning("check_pr_evidence_fresh_shape: no client provided")
        return None

    # Look for explicit branch directive in goal (e.g., "(branch=feature-xyz)")
    branch_match = re.search(r"\(branch=([^)]+)\)", goal)
    if branch_match:
        branch = branch_match.group(1).strip()
    else:
        # Default convention
        branch = f"task/{task_id}"

    try:
        pr = client.get_pull_by_head_branch(branch)
        if not pr:
            return False
        created_at_str = pr.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                return created_at > spawned_at
            except (ValueError, AttributeError):
                # Present-but-unparseable timestamp: freshness CANNOT be verified,
                # so evidence is genuinely unknown → escalate (None), NOT True.
                # Silently treating a garbage timestamp as fresh evidence would
                # let a stale reused-branch PR pass the gate (MEDIUM, PR #1011).
                # This is distinct from an ABSENT created_at below, where the
                # per-task-unique branch name is a legitimate fallback signal.
                logger.warning(
                    "check_pr_evidence_fresh_shape: unparseable created_at %r for %s; "
                    "cannot verify freshness — escalating (None)",
                    created_at_str,
                    task_id,
                )
                return None
        # No created_at field at all: the per-task-unique branch name is itself
        # strong evidence, so fall back to treating existence as evidence.
        return True
    except Exception:
        logger.exception("check_pr_evidence_fresh_shape: client error for %s", task_id)
        return None


def check_pr_evidence_rework_shape(
    task_id: str,
    goal: str,
    pr_number: int,
    spawned_at: datetime,
    *,
    client: GitHubClient | None = None,
) -> bool | None:
    """Check PR evidence for rework-shape goals (AC2 #953).

    Rework-shape goals directly reference a PR number (e.g., '/rework #42').
    Evidence check: PR #N exists AND has activity (commits/updatedAt) after
    the sidecar's spawned_at timestamp.

    Returns:
    - True: PR exists with new activity since spawned_at
    - False: PR missing or no new activity
    - None: Error/unparseable (treated as escalate)
    """
    if client is None:
        logger.warning("check_pr_evidence_rework_shape: no client provided")
        return None

    try:
        pr = client.get_pull_by_number(pr_number)
        if not pr:
            return False

        # Check if PR was updated after spawned_at
        updated_at_str = pr.get("updated_at")
        if updated_at_str:
            try:
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                if updated_at > spawned_at:
                    return True
            except (ValueError, AttributeError):
                logger.warning(
                    "check_pr_evidence_rework_shape: unparseable updated_at for PR #%s; "
                    "falling through to commit-activity channel",
                    pr_number,
                )

        # Check if there are new commits since spawned_at. The commit list is
        # the only remaining evidence channel here (updated_at gave no positive
        # signal). If the fetch fails we do NOT know whether new commits exist —
        # falling through to ``return False`` would be a confident "no activity"
        # verdict built on an unknown, spuriously re-driving. Tri-state demands
        # None so the orchestrator escalates instead (MAJOR, #1011).
        try:
            commits = client.list_commits_for_pull(pr_number)
        except Exception:
            logger.exception(
                "check_pr_evidence_rework_shape: commit fetch failed for PR #%s", pr_number
            )
            return None
        for commit in commits:
            commit_date_str = commit.get("commit", {}).get("author", {}).get("date")
            if commit_date_str:
                try:
                    commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                    if commit_date > spawned_at:
                        return True
                except (ValueError, AttributeError):
                    continue

        return False
    except Exception:
        logger.exception("check_pr_evidence_rework_shape: client error for PR #%s", pr_number)
        return None


class HttpxGitHubClient:
    """Concrete :class:`GitHubClient` over the GitHub REST API (AC4 #953).

    The driver computes PR evidence through this — *no* ``gh`` CLI subprocess
    (AC4). It is the production injection point; the pure evidence checks in
    this module take a ``GitHubClient`` so tests inject fakes and never touch
    the network. The three methods are thin GETs; 404 is normalized to the
    "absent" sentinel each caller expects (``None`` / ``None`` / ``[]``).
    """

    def __init__(self, repo: str, *, token: str | None = None, timeout: float = 10.0) -> None:
        self._repo = repo
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._timeout = timeout
        # A pooled client reuses TCP/TLS connections across the (potentially
        # several, paginated) GETs each evidence check fires. The wake_driver
        # holds one instance for its lifetime, so per-request connection setup
        # was pure overhead. Headers/timeout are baked in so call sites stay thin.
        self._client = httpx.Client(headers=_headers(self._token), timeout=self._timeout)

    def close(self) -> None:
        """Release the pooled connections. Idempotent — safe to call twice."""
        self._client.close()

    @property
    def _owner(self) -> str:
        return self._repo.split("/", 1)[0]

    def get_pull_by_head_branch(self, branch: str) -> dict[str, Any] | None:
        # head filter wants ``owner:branch``; state=all so a merged/closed PR
        # still counts as evidence that work landed.
        resp = self._client.get(
            f"https://api.github.com/repos/{self._repo}/pulls",
            params={"head": f"{self._owner}:{branch}", "state": "all", "per_page": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None

    def get_pull_by_number(self, pr_number: int) -> dict[str, Any] | None:
        resp = self._client.get(
            f"https://api.github.com/repos/{self._repo}/pulls/{pr_number}",
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def list_commits_for_pull(self, pr_number: int) -> list[dict[str, Any]]:
        # The PR commits endpoint returns commits OLDEST-first, paginated at
        # per_page<=100 and capped at 250 total (<=3 pages at the cap). A single
        # ``per_page=100`` GET returns only the OLDEST 100 — exactly the commits
        # that predate ``spawned_at`` — so on a >100-commit PR the freshness
        # check would miss every recent commit and falsely report "no new
        # activity", spuriously re-driving (CRITICAL, PR #1011 round 2; the
        # round-1 "page 1 + last page" fix silently dropped every intermediate
        # page). This endpoint does NOT accept a ``?since=`` filter (that exists
        # only on the repo-level /commits endpoint), so the only correct fix is
        # to walk every page and let the caller apply the timestamp gate. At the
        # 250 cap that is <=3 GETs.
        url = f"https://api.github.com/repos/{self._repo}/pulls/{pr_number}/commits"
        commits: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self._client.get(url, params={"per_page": 100, "page": page})
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            page_commits = resp.json()
            if not page_commits:
                break
            commits.extend(page_commits)
            if len(page_commits) < 100:
                break
            page += 1
        return commits

    def list_open_pulls(self) -> list[dict[str, Any]]:
        """All open PRs as ``{number, body, headRefName}`` dicts (#931 dedup).

        The dispatch-dedup predicate (:func:`delegate_predispatch_gate.check_in_flight`)
        matches on a PR's closing-keyword body and its head branch under the
        ``headRefName`` key — the same shape ``gh pr list --json`` emits. The REST
        list endpoint instead nests the branch under ``head.ref``, so each row is
        remapped. Paginated at ``per_page=100`` (full walk, short page terminates)
        so a busy repo's later PRs are never silently dropped.
        """
        url = f"https://api.github.com/repos/{self._repo}/pulls"
        pulls: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self._client.get(url, params={"state": "open", "per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for pr in batch:
                pulls.append(
                    {
                        "number": pr.get("number"),
                        "body": pr.get("body") or "",
                        "headRefName": (pr.get("head") or {}).get("ref"),
                    }
                )
            if len(batch) < 100:
                break
            page += 1
        return pulls

    def list_branch_names(self) -> list[str]:
        """All branch names (#931 dedup).

        Backs the head-branch arm of the dedup predicate — an in-flight branch
        (``feat/<N>-``) with no PR yet still means the issue is being worked.
        Paginated like :meth:`list_open_pulls`.
        """
        url = f"https://api.github.com/repos/{self._repo}/branches"
        names: list[str] = []
        page = 1
        while True:
            resp = self._client.get(url, params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            names.extend(b["name"] for b in batch)
            if len(batch) < 100:
                break
            page += 1
        return names


def default_github_client() -> HttpxGitHubClient:
    """Production :class:`GitHubClient` — repo from ``GITHUB_REPO`` env (AC4).

    Defaults to ``your-username/your-repo`` (edit for your fork); token resolved
    from ``GITHUB_TOKEN`` inside
    :class:`HttpxGitHubClient`. Needs live network, so it is wired from
    :func:`wake_driver.main` and never exercised by unit tests (which inject
    fakes into the pure checks above)."""
    return HttpxGitHubClient(os.environ.get("GITHUB_REPO", "your-username/your-repo"))
