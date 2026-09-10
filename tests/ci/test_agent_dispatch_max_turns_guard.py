"""Guard for the worker step's ``--max-turns`` ceiling in agent-dispatch.yml.

#1846: the trial run (#1806, issue #1844 -> PR #1845) did entirely correct,
right-scoped work -- 8/8 check-runs green, auto-merge succeeded -- and still
burned 51 turns on a one-line docs fix, tripping the old ``--max-turns 40``.
``claude-code-action`` checks the flag *after* a successful result and fails
the "Run unattended worker" step on the overage, so a run that did the right
thing end to end still read red. That is a silent-signal-decay risk, not a
cosmetic one: once "worker's red again, but the PR merged fine" becomes the
normal read, a genuine broken-worker run drowns in the same noise.

The fix chosen here is to raise the ceiling rather than swallow the specific
"successful result after N turns" error as non-fatal (the alternative the
issue also considered) -- if a trivial task already needed 51 turns, 40 was
never going to hold for anything harder, so the honest fix is more headroom,
not muting the false positive at this threshold. This test pins the chosen
value so a regression back toward the old ceiling (or any future edit that
quietly lowers it again) fails loudly instead of waiting for the next real
run to trip it.

Convention: docs/reference/ci-guard-meta-tests.md (#326) covers PR-blocking
`paths:`-filtered workflows specifically; agent-dispatch.yml fires on
`issues: labeled`, so it is out of that convention's scope, but it already
has a co-located meta-test (test_agent_dispatch_automerge_guard.py) -- this
file follows the same one-workflow-many-narrow-guards shape for a second,
unrelated invariant on the same workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "agent-dispatch.yml"

STEP_NAME = "Run unattended worker"

# The floor the trial run (#1806) proved 40 was too low against: a one-line
# docs fix -- the simplest possible AC -- burned 51 turns. Anything at or
# below that floor is a known-reproducible false red, not just "risky".
KNOWN_FALSE_RED_TURN_COUNT = 51


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def worker_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["worker"]["steps"]
    for step in steps:
        if step.get("name") == STEP_NAME:
            return step
    pytest.fail(f"agent-dispatch.yml has no {STEP_NAME!r} step; #1846 context regressed")


def _max_turns(claude_args: str) -> int:
    match = re.search(r"--max-turns\s+(\d+)", claude_args)
    assert match, "worker step must set --max-turns explicitly (#1846)"
    return int(match.group(1))


class TestMaxTurnsCeiling:
    def test_max_turns_is_pinned_value(self, worker_step: dict):
        claude_args = worker_step["with"]["claude_args"]
        assert _max_turns(claude_args) == 100, (
            "agent-dispatch.yml's --max-turns changed; update this pin deliberately "
            "(with a rationale in the workflow's own comment) rather than drifting"
        )

    def test_max_turns_clears_the_known_false_red(self, worker_step: dict):
        claude_args = worker_step["with"]["claude_args"]
        assert _max_turns(claude_args) > KNOWN_FALSE_RED_TURN_COUNT, (
            f"a ceiling at or below {KNOWN_FALSE_RED_TURN_COUNT} reproduces the exact "
            "false red #1846 was filed for -- a trivial docs fix already needed that many turns"
        )

    def test_timeout_minutes_unaffected(self, workflow: dict):
        # #1846's root cause was the turn ceiling, not wall-clock time -- the
        # trial run finished in ~3 of its 45 allotted minutes. Nothing here
        # licenses raising timeout-minutes too; pin it so a future edit
        # doesn't conflate the two independent limits.
        assert workflow["jobs"]["worker"]["timeout-minutes"] == 45
