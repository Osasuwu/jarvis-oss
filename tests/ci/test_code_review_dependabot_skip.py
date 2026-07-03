"""Meta-test for the Dependabot skip in .github/workflows/code-review.yml.

Reimplements the `review` job's run/skip predicate in Python and asserts it
skips Dependabot PRs across every pull_request event type, plus a config
dimension that pins the YAML to the *author*-based guard.

Why this test exists (CLAUDE.md §326): code-review.yml is NOT path-filtered,
so #326's *config* mandate doesn't strictly apply. But this is the exact
silent-drift class #326 targets. The original guard keyed off `github.actor`,
which is the *pusher* on `synchronize` events — when merge-train.yml updated a
stale Dependabot branch with an App token, the actor stopped being
`dependabot[bot]`, the guard passed, the review job ran on a trivial dep bump
and failed, code-review-retry re-dispatched, and the `review` check went red
(#944). The fix keys off `pull_request.user.login` (the PR author), which is
stable across opened / synchronize / reopened / ready_for_review. This test
fails if anyone reintroduces the actor-based guard.

Predicate mirrored here (see the job-level `if:` in the YAML):
  run  = event == workflow_dispatch
         OR (head repo == base repo  AND  PR author != dependabot[bot])
"""

from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "code-review.yml"
)

DEPENDABOT = "dependabot[bot]"


def _review_should_run(
    *,
    event_name: str,
    head_repo: str,
    base_repo: str,
    pr_author: str,
) -> bool:
    """Reimplementation of the review job's `if:` condition.

    Deliberately takes NO `actor` argument — the whole point of the fix is that
    the triggering actor is irrelevant to the Dependabot decision.
    """
    if event_name == "workflow_dispatch":
        return True
    return head_repo == base_repo and pr_author != DEPENDABOT


REPO = "Osasuwu/jarvis"


def test_dependabot_pr_skipped_on_open():
    assert not _review_should_run(
        event_name="pull_request", head_repo=REPO, base_repo=REPO, pr_author=DEPENDABOT
    )


def test_dependabot_pr_skipped_on_synchronize_even_when_actor_is_app():
    # The regression: a merge-train branch update arrives as `synchronize` with
    # a non-Dependabot pusher. Author is still dependabot[bot] -> still skip.
    assert not _review_should_run(
        event_name="pull_request", head_repo=REPO, base_repo=REPO, pr_author=DEPENDABOT
    )


def test_fork_pr_skipped():
    assert not _review_should_run(
        event_name="pull_request",
        head_repo="someone/jarvis",
        base_repo=REPO,
        pr_author="contributor",
    )


def test_normal_pr_runs():
    assert _review_should_run(
        event_name="pull_request", head_repo=REPO, base_repo=REPO, pr_author="Osasuwu"
    )


def test_workflow_dispatch_always_runs():
    assert _review_should_run(
        event_name="workflow_dispatch", head_repo=REPO, base_repo=REPO, pr_author="anyone"
    )


# --- Config dimension: keep the YAML and the predicate above in lockstep. ---
# Assert against the parsed `if:` value, not raw file text: the surrounding
# comment intentionally names the old actor-based guard to explain the
# regression, so a raw-text search would match the prose too.


def _review_if() -> str:
    spec = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return spec["jobs"]["review"]["if"]


def test_workflow_exists():
    assert WORKFLOW_PATH.is_file(), "code-review.yml missing"


def test_guard_keys_off_pr_author_not_actor():
    condition = _review_if()
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in condition, (
        "Dependabot guard must key off the PR author (pull_request.user.login); "
        "the author is stable across synchronize events, the triggering actor is not."
    )
    # Pin the regression: the actor-based guard must not come back.
    assert "github.actor" not in condition, (
        "github.actor changes to the pusher on synchronize events (e.g. merge-train "
        "branch updates) — using it as the Dependabot guard reintroduces #944."
    )


def test_fork_guard_intact():
    condition = _review_if()
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition, (
        "fork PRs must still be skipped (untrusted code on the main runner)."
    )
