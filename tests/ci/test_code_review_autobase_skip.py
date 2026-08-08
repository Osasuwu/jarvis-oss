"""Meta-test: auto-rebase push skip in .github/workflows/code-review.yml.

jarvis-ci[bot] exclusively pushes "Merge branch 'main' into <feature>"
rebases — no new code. Without this guard each rebase triggers a full review
run: PR #963 accumulated 28 auto-rebase pushes → 23 spurious review comments
despite only 4-5 real rework rounds (incident 2026-06-29).

The guard must live at STEP level (not job `if:`). The job-level condition
must not use github.actor — test_code_review_dependabot_skip.py already pins
that invariant for the #944 regression. Adding actor to job `if:` would
collide with the stable-PR-author contract for dependabot[bot].

Predicates mirrored here:
  autobase_skip  = event == pull_request AND actor == 'jarvis-ci[bot]'
  review_runs    = NOT autobase_skip AND (workflow_dispatch OR has_code)
  verdict_runs   = pull_request OR workflow_dispatch  (#1134: the verdict step
                   now RUNS on the autobase push too — it re-enforces the last
                   real review verdict against the last-non-bot head anchor
                   instead of skipping, which let #1131 auto-merge past a live
                   CRITICAL — and branches on autobase_skip INTERNALLY.)
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "code-review.yml"

AUTOBASE_BOT = "jarvis-ci[bot]"
AUTOBASE_STEP_ID = "autobase"


def _is_autobase_push(*, event_name: str, actor: str) -> bool:
    return event_name == "pull_request" and actor == AUTOBASE_BOT


def _review_should_run(
    *,
    event_name: str,
    actor: str,
    has_code: bool,
) -> bool:
    if _is_autobase_push(event_name=event_name, actor=actor):
        return False
    return event_name == "workflow_dispatch" or has_code


def _verdict_should_run(*, event_name: str, actor: str) -> bool:
    # #1134: the verdict step now RUNS on the autobase push. It no longer
    # short-circuits on the jarvis-ci[bot] actor — instead it runs and, on that
    # push, anchors freshness on the last non-bot head (see the run body). The
    # bug it fixes: skipping here left the `review` check green with nothing
    # evaluated, so native auto-merge shipped #1131 past a live CRITICAL. `actor`
    # is retained for call-site symmetry with `_review_should_run`.
    del actor
    return event_name in ("pull_request", "workflow_dispatch")


# --- Logic tests ---


def test_autobase_bot_synchronize_skips_review():
    assert not _review_should_run(event_name="pull_request", actor=AUTOBASE_BOT, has_code=True)


def test_autobase_bot_synchronize_runs_verdict():
    # #1134: the verdict step now RUNS on the autobase push so it can re-enforce
    # the last real review verdict against the last-non-bot head anchor. It used
    # to skip — leaving `review` green with nothing evaluated → #1131 auto-merged
    # past a live CRITICAL.
    assert _verdict_should_run(event_name="pull_request", actor=AUTOBASE_BOT)


def test_human_push_with_code_runs_review():
    assert _review_should_run(event_name="pull_request", actor="your-username", has_code=True)


def test_human_push_no_code_skips_review():
    assert not _review_should_run(event_name="pull_request", actor="your-username", has_code=False)


def test_workflow_dispatch_always_runs_review():
    # Retry path (github-actions[bot]) must never be blocked by the autobase guard.
    assert _review_should_run(
        event_name="workflow_dispatch", actor="github-actions[bot]", has_code=False
    )


def test_workflow_dispatch_always_runs_verdict():
    assert _verdict_should_run(event_name="workflow_dispatch", actor="github-actions[bot]")


def test_jarvis_agent_push_runs_review():
    # Real rework commits come from "Jarvis Agent" (git author), but the
    # github.actor on the push is the owner or a PAT — never the CI bot.
    assert _review_should_run(event_name="pull_request", actor="your-username", has_code=True)


# --- Config dimension: pin the YAML structure ---


def _load_steps() -> list[dict]:
    spec = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return spec["jobs"]["review"]["steps"]


def _step_by_id(steps: list[dict], step_id: str) -> dict | None:
    return next((s for s in steps if s.get("id") == step_id), None)


def _step_by_name(steps: list[dict], name: str) -> dict | None:
    return next((s for s in steps if s.get("name") == name), None)


def test_workflow_exists():
    assert WORKFLOW_PATH.is_file(), "code-review.yml missing"


def test_autobase_step_exists():
    steps = _load_steps()
    step = _step_by_id(steps, AUTOBASE_STEP_ID)
    assert step is not None, f"Step with id='{AUTOBASE_STEP_ID}' not found in review job"


def test_autobase_step_condition_references_bot():
    steps = _load_steps()
    step = _step_by_id(steps, AUTOBASE_STEP_ID)
    condition = str(step.get("if", ""))
    assert AUTOBASE_BOT in condition, (
        f"autobase step must check github.actor == '{AUTOBASE_BOT}'; got: {condition!r}"
    )


def test_autobase_step_condition_is_pull_request_scoped():
    steps = _load_steps()
    step = _step_by_id(steps, AUTOBASE_STEP_ID)
    condition = str(step.get("if", ""))
    assert "pull_request" in condition, (
        "autobase step must only fire on pull_request events "
        "(workflow_dispatch retries must not be blocked)"
    )


def test_review_step_gated_on_autobase():
    steps = _load_steps()
    step = _step_by_name(steps, "Run /code-review")
    assert step is not None, "Run /code-review step not found"
    condition = str(step.get("if", ""))
    assert "steps.autobase.outputs.skip" in condition, (
        "Run /code-review step must gate on steps.autobase.outputs.skip != 'true'"
    )


def test_verdict_step_runs_on_autobase_but_branches_internally():
    # #1134: the verdict step's if: must NOT gate on steps.autobase.outputs.skip
    # (so it RUNS on the autobase push), but the flag must still reach the step
    # (env or run body) so it can branch the freshness anchor internally.
    steps = _load_steps()
    step = _step_by_name(steps, "Verify review verdict")
    assert step is not None, "Verify review verdict step not found"
    condition = str(step.get("if", ""))
    assert "steps.autobase.outputs.skip" not in condition, (
        "Verify review verdict must RUN on the autobase push — drop the "
        "steps.autobase.outputs.skip gate from its if: (#1134). Skipping there "
        "let #1131 auto-merge past a CRITICAL (`review` green, nothing evaluated)."
    )
    env = step.get("env", {}) or {}
    in_env = any("steps.autobase.outputs.skip" in str(v) for v in env.values())
    in_run = "steps.autobase.outputs.skip" in str(step.get("run", ""))
    assert in_env or in_run, (
        "Verdict step must still reference steps.autobase.outputs.skip (via env "
        "or run body) to branch the freshness anchor on the autobase path."
    )


def test_actor_not_in_job_level_if():
    """Regression pin: github.actor must stay out of the job-level `if:`.

    The dependabot[bot] guard keys off PR author (stable across synchronize
    events). Mixing in github.actor at the job level would reintroduce #944.
    test_code_review_dependabot_skip.py already asserts this, but we pin it
    here too so the intent is visible from this file.
    """
    spec = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    job_if = str(spec["jobs"]["review"].get("if", ""))
    assert "github.actor" not in job_if, (
        "github.actor must not appear in the review job-level `if:` — "
        "it changes to the pusher on synchronize events (#944). "
        "Use step-level conditions for actor-based filtering."
    )
