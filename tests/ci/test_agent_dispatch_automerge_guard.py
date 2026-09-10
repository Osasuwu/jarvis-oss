"""Meta-test for the auto-merge post-step in .github/workflows/agent-dispatch.yml.

After #1796 culled the 14 legacy workflows, nothing in this repo enabled
auto-merge any more: the unattended worker ran to ``gh pr create`` and stopped,
so its PR parked until a human clicked merge. That made the third clause of
#1806's AC2 ("PR смержен auto-merge") unreachable by construction — and, worse,
unreachable *silently*: the worker step goes green either way, so "the lane
works end to end" and "the lane opens PRs nobody merges" look identical on the
Actions page.

Two things are pinned here, because both are load-bearing and neither errors
when it drifts:

1. **The branch-selection rule.** The step has to find the worker's PR without
   knowing the slug the worker chose, so it matches ``claude/issue-<N>``
   exactly or with a ``-<slug>`` suffix. Drop the trailing ``-`` from the
   prefix test and a run dispatched for issue 180 happily queues issue 1806's
   PR. ``select_pr`` below mirrors the awk rule; the table exercises the
   collision case directly.

2. **The step's shape.** ``if: always()`` (a worker that opened its PR and then
   hit the 45-minute timeout must still get it queued), ``--auto`` and not a
   bare merge (``--auto`` queues behind the required checks; a bare
   ``gh pr merge`` would merge on the spot and bypass every gate), and the PAT
   rather than ``GITHUB_TOKEN`` (a GITHUB_TOKEN-authored merge commit does not
   trigger push workflows on ``main`` — the same GitHub loop-prevention that
   #1805 worked around for the worker's own push).

Also pinned: ``gh pr merge`` stays OUT of the worker agent's ``--allowed-tools``.
Queueing the merge is the workflow's job, not a capability handed to the
headless agent's shell — an agent that can call ``gh pr merge`` can call it
without ``--auto``.

Convention: docs/reference/ci-guard-meta-tests.md (#326).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "agent-dispatch.yml"

STEP_NAME = "Queue auto-merge on the worker's PR"


def select_pr(open_prs: list[tuple[str, int]], prefix: str) -> int | None:
    """Mirror the step's awk rule: first open PR on the worker's branch.

    ``open_prs`` is (head_ref, number) in the order ``gh pr list`` returns.
    """
    for head_ref, number in open_prs:
        if head_ref == prefix or head_ref.startswith(prefix + "-"):
            return number
    return None


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def automerge_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["worker"]["steps"]
    for step in steps:
        if step.get("name") == STEP_NAME:
            return step
    pytest.fail(f"agent-dispatch.yml has no {STEP_NAME!r} step; #1806 AC2 regressed")


class TestBranchSelection:
    @pytest.mark.parametrize(
        ("open_prs", "prefix", "expected"),
        [
            # Normal case: worker branch carries a slug.
            ([("claude/issue-1806-automerge", 42)], "claude/issue-1806", 42),
            # Slugless branch still matches.
            ([("claude/issue-1806", 42)], "claude/issue-1806", 42),
            # The collision the trailing `-` exists to prevent: dispatched for
            # issue 180, must NOT queue issue 1806's PR.
            ([("claude/issue-1806-automerge", 42)], "claude/issue-180", None),
            # Unrelated branches are ignored.
            ([("main", 1), ("feat/whatever", 2)], "claude/issue-1806", None),
            # Worker escalated per step 5 — no PR at all.
            ([], "claude/issue-1806", None),
            # First match wins, and only the worker's own branch matches.
            (
                [("feat/other", 7), ("claude/issue-1806-a", 8), ("claude/issue-1806-b", 9)],
                "claude/issue-1806",
                8,
            ),
        ],
    )
    def test_select_pr(self, open_prs, prefix, expected):
        assert select_pr(open_prs, prefix) == expected


class TestStepShape:
    def test_runs_even_when_worker_step_failed(self, automerge_step):
        # A worker that opened its PR and then timed out must still get it
        # queued; without `always()` the step is skipped on any worker failure.
        assert str(automerge_step.get("if")).strip() == "always()"

    def test_queues_rather_than_merging_outright(self, automerge_step):
        run = automerge_step["run"]
        assert "--auto" in run, (
            "`gh pr merge` without --auto merges immediately, bypassing every required check"
        )
        assert re.search(r"gh pr merge\b[^\n]*--auto", run), (
            "the merge invocation itself must carry --auto"
        )

    def test_uses_pat_not_github_token(self, automerge_step):
        token = automerge_step["env"]["GH_TOKEN"]
        assert "AGENT_DISPATCH_PAT" in token
        assert "GITHUB_TOKEN" not in token

    def test_branch_prefix_matches_worker_convention(self, automerge_step):
        prefix = automerge_step["env"]["PR_BRANCH_PREFIX"]
        assert prefix.startswith("claude/issue-"), (
            "prefix must track the branch name the worker prompt tells the agent to create"
        )
        assert "github.event.issue.number" in prefix


class TestAgentCannotMergeItself:
    def test_gh_pr_merge_absent_from_allowed_tools(self, workflow: dict):
        steps = workflow["jobs"]["worker"]["steps"]
        worker = next(s for s in steps if s.get("name") == "Run unattended worker")
        claude_args = worker["with"]["claude_args"]
        assert "gh pr merge" not in claude_args, (
            "queueing auto-merge is the workflow's job; an agent allowed `gh pr merge` "
            "can call it without --auto and merge past every gate"
        )
