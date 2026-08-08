"""Meta-test for scripts/code_review_retry.py + code-review.yml retry job (#807).

Two halves:
  - Pure decision/parsing logic in isolation (no gh subprocess).
  - Workflow file wiring: the retry job exists, gates on workflow_run+failure,
    has actions:write, and invokes the script.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts.code_review_retry import (
    MAX_ATTEMPTS,
    classify_failure_signature,
    decide,
    parse_reset_time_utc,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review-retry.yml"
REVIEW_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"


# -- Decision logic ----------------------------------------------------------
#
# #1325: attempt counting moved from summing failed-conclusion rows fetched via
# the runs-list API to the `run_attempt` field GitHub stamps on the
# workflow_run webhook payload itself — `gh run rerun --failed` mutates the
# SAME run (incrementing run_attempt) rather than creating a new run row, so
# the old runs-list-based count silently stayed at 1 forever post-#1325's
# dispatch-path fix. decide() also gained a stale-head guard: if the PR's
# HEAD moved past the failed run's SHA (a new push landed before the retry
# fired), the retry is for a commit nobody cares about anymore — skip it.

PR = {"number": 123, "headRefName": "feat/foo", "headRefOid": "abc123"}


class TestDecide:
    def test_first_attempt_dispatches(self):
        d = decide([PR], "feat/foo", "abc123", run_attempt=1)
        assert d.kind == "dispatch"
        assert d.pr_number == 123

    def test_below_cap_still_dispatches(self):
        # MAX_ATTEMPTS=4 (includes triggering run). attempt 3 → still below cap.
        d = decide([PR], "feat/foo", "abc123", run_attempt=MAX_ATTEMPTS - 1)
        assert d.kind == "dispatch"

    def test_at_cap_marks_exhausted(self):
        d = decide([PR], "feat/foo", "abc123", run_attempt=MAX_ATTEMPTS)
        assert d.kind == "exhausted"
        assert d.pr_number == 123

    def test_over_cap_marks_exhausted(self):
        d = decide([PR], "feat/foo", "abc123", run_attempt=MAX_ATTEMPTS + 5)
        assert d.kind == "exhausted"

    def test_max_attempts_4_permits_3_retries(self):
        # Finding #3: MAX_ATTEMPTS=4 counts the triggering run, yielding 3 actual
        # retries. Verify the boundary: attempt 3 dispatches; attempt 4 exhausts.
        assert MAX_ATTEMPTS == 4, "MAX_ATTEMPTS must be 4 to permit 3 retries"
        assert decide([PR], "feat/foo", "abc123", run_attempt=3).kind == "dispatch"
        assert decide([PR], "feat/foo", "abc123", run_attempt=4).kind == "exhausted"

    def test_skip_when_no_open_pr_for_branch(self):
        d = decide([], "feat/orphan", "abc123", run_attempt=1)
        assert d.kind == "skip"
        assert d.pr_number is None

    def test_skip_when_branch_doesnt_match_any_open_pr(self):
        d = decide([PR], "feat/other-branch", "abc123", run_attempt=1)
        assert d.kind == "skip"

    def test_skip_when_head_sha_stale(self):
        # PR's current HEAD (headRefOid) has moved past the failed run's SHA —
        # a new push landed before this retry fired. Rerunning a stale commit's
        # check is pointless (#1325 Finding 1 follow-on: without this guard a
        # slow quota-wait retry could fire against an abandoned commit).
        d = decide([PR], "feat/foo", "stale-sha-from-old-push", run_attempt=1)
        assert d.kind == "skip"
        assert "stale" in d.reason.lower() or "head" in d.reason.lower()

    def test_matching_head_sha_dispatches(self):
        d = decide([PR], "feat/foo", "abc123", run_attempt=1)
        assert d.kind == "dispatch"


# -- Reset-time parsing ------------------------------------------------------


class TestParseResetTimeUtc:
    def test_returns_none_when_no_signature(self):
        now = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
        assert parse_reset_time_utc("just a generic error", now) is None

    def test_parses_am_signature_later_today(self):
        # The actual error string we captured from a real failed run.
        log = "Claude Code returned an error result: You've hit your session limit · resets 3:40am (UTC)"
        now = datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc)
        reset = parse_reset_time_utc(log, now)
        assert reset == datetime(2026, 5, 27, 3, 40, tzinfo=timezone.utc)

    def test_parses_pm_signature(self):
        log = "session limit · resets 4:15pm (UTC)"
        now = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        reset = parse_reset_time_utc(log, now)
        assert reset == datetime(2026, 5, 27, 16, 15, tzinfo=timezone.utc)

    def test_returns_none_when_reset_already_passed(self):
        # Previously rolled to next day; now returns None so caller retries
        # immediately rather than sleeping ~24h (finding #5 fix).
        log = "session limit · resets 3:40am (UTC)"
        now = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)  # past 03:40
        assert parse_reset_time_utc(log, now) is None

    def test_parses_24h_signature_without_ampm(self):
        log = "session limit · resets 14:30 (UTC)"
        now = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        reset = parse_reset_time_utc(log, now)
        assert reset == datetime(2026, 5, 27, 14, 30, tzinfo=timezone.utc)

    def test_midnight_12am_already_passed_returns_none(self):
        log = "session limit · resets 12:00am (UTC)"
        now = datetime(2026, 5, 27, 23, 30, tzinfo=timezone.utc)
        # 12:00am today (00:00) has already passed; returns None (retry immediately).
        assert parse_reset_time_utc(log, now) is None

    def test_midnight_12am_is_hour_0_future(self):
        log = "session limit · resets 12:00am (UTC)"
        now = datetime(2026, 5, 27, 23, 59, 59, tzinfo=timezone.utc)
        # Still in the future if 00:00 hasn't arrived yet — but 00:00 is the
        # start of 27th, which is already past. Returns None.
        assert parse_reset_time_utc(log, now) is None

    def test_noon_12pm_is_hour_12(self):
        log = "session limit · resets 12:00pm (UTC)"
        now = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        reset = parse_reset_time_utc(log, now)
        assert reset == datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)

    def test_rejects_garbage_hour(self):
        log = "session limit · resets 25:99 (UTC)"
        now = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        assert parse_reset_time_utc(log, now) is None

    def test_rejects_13am_invalid_12h_clock(self):
        # Finding #6: two-digit hour 13–19 paired with am/pm has no valid
        # interpretation. Must return None, not silently misparse.
        log = "session limit · resets 13:00am (UTC)"
        now = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        assert parse_reset_time_utc(log, now) is None

    def test_rejects_19pm_invalid_12h_clock(self):
        log = "session limit · resets 19:30pm (UTC)"
        now = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        assert parse_reset_time_utc(log, now) is None


# -- Rerun-in-place dispatch (#1325 Option A) --------------------------------
#
# The retry used to fire a FRESH `gh workflow run` dispatch, which creates a
# new run (and a new `review` check-run) alongside the earlier failing one —
# the stale failure never gets cleared, leaving mergeStateStatus BLOCKED even
# after the retry succeeds (#1325 Finding 2). `gh run rerun <id> --failed`
# instead reruns the SAME run in place: one check-run, updated conclusion.


class TestDispatch:
    def test_dispatch_reruns_the_failed_run_by_id(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "scripts.code_review_retry.subprocess.check_call",
            lambda argv: calls.append(argv),
        )
        from scripts.code_review_retry import _dispatch

        _dispatch("owner/repo", "999888")

        assert len(calls) == 1
        argv = calls[0]
        assert argv[:3] == ["gh", "run", "rerun"]
        assert "999888" in argv
        assert "--repo" in argv and "owner/repo" in argv
        assert "--failed" in argv
        # Must NOT be the old fresh-dispatch form.
        assert "workflow" not in argv


# -- Double-dispatch guard (finding #2) -------------------------------------


class TestDoubleDispatchGuard:
    """Guard must fire for immediate-retry path (reset_at=None), not just post-sleep."""

    def _make_runs(self, status: str, conclusion: str | None = None) -> list[dict]:
        return [{"status": status, "conclusion": conclusion}]

    def test_queued_run_blocks_dispatch(self):
        """If a run is already queued, dispatch must not happen.

        This exercises the pure logic: if any run has status=queued or
        in_progress, the pre-dispatch re-check should prevent dispatch.
        The guard now runs unconditionally before _dispatch(), covering
        the immediate-retry path (no quota sleep).
        """
        runs = self._make_runs("queued")
        blocked = any(r.get("status") in ("queued", "in_progress") for r in runs)
        assert blocked, "queued run must block dispatch"

    def test_in_progress_run_blocks_dispatch(self):
        runs = self._make_runs("in_progress")
        blocked = any(r.get("status") in ("queued", "in_progress") for r in runs)
        assert blocked, "in_progress run must block dispatch"

    def test_completed_failure_does_not_block_dispatch(self):
        runs = self._make_runs("completed", conclusion="failure")
        blocked = any(r.get("status") in ("queued", "in_progress") for r in runs)
        assert not blocked, "a completed failure must not block the retry dispatch"

    def test_success_run_blocks_dispatch(self):
        runs = [{"status": "completed", "conclusion": "success"}]
        success_exists = any(r.get("conclusion") == "success" for r in runs)
        assert success_exists, "success run must block dispatch"


# -- Failure classification (#1325 Option C') --------------------------------
#
# The retry mechanism has a historically 0% observed success rate. Before
# this, a failed retry left no trace of *why* dispatch didn't help — was it
# still quota, a permission error the retry can't fix by definition, or
# something novel? classify_failure_signature() gives the workflow logs (and
# a future follow-up issue) a structured signal instead of raw log grepping.


class TestClassifyFailureSignature:
    def test_quota_reset_signature_classified(self):
        log = "Claude Code returned an error result: You've hit your session limit · resets 3:40am (UTC)"
        assert classify_failure_signature(log) == "quota-reset"

    def test_permission_denied_signature_classified(self):
        log = '{"message":"Resource not accessible by integration","status":"403"}'
        assert classify_failure_signature(log) == "permission-denied"

    def test_bad_credentials_signature_classified(self):
        log = "gh: Bad credentials (HTTP 401)"
        assert classify_failure_signature(log) == "permission-denied"

    def test_empty_log_classified_as_no_log_available(self):
        assert classify_failure_signature("") == "no-log-available"

    def test_unrecognized_log_classified_as_unknown(self):
        log = "Error: connect ECONNRESET 10.0.0.1:443"
        assert classify_failure_signature(log) == "unknown"

    def test_quota_reset_takes_precedence_over_generic_text(self):
        log = (
            "some unrelated noise\n"
            "Claude Code returned an error result: You've hit your session limit · resets 11:15pm (UTC)\n"
            "more noise"
        )
        assert classify_failure_signature(log) == "quota-reset"


# -- Workflow wiring ---------------------------------------------------------


@pytest.fixture(scope="module")
def retry_workflow() -> dict:
    return yaml.safe_load(RETRY_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def review_workflow() -> dict:
    return yaml.safe_load(REVIEW_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML parses bare `on:` as Python True (YAML 1.1 boolean).
    return workflow.get("on") or workflow.get(True)


class TestRetryWorkflowWiring:
    def test_retry_workflow_exists(self):
        assert RETRY_WORKFLOW.exists(), (
            "Retry workflow split out into its own file because GitHub rejects "
            "workflows that declare workflow_run triggers on themselves "
            "(validation failure with 0 jobs)."
        )

    def test_listens_to_code_review_completion(self, retry_workflow):
        on = _triggers(retry_workflow)
        assert "workflow_run" in on
        wr = on["workflow_run"]
        assert "Code Review" in wr.get("workflows", []), (
            "Retry must listen to the Code Review workflow by name"
        )
        assert "completed" in wr.get("types", [])

    def test_retry_job_gates_on_failure(self, retry_workflow):
        retry = retry_workflow["jobs"]["retry"]
        gate = retry["if"]
        assert "conclusion == 'failure'" in gate
        # Default-branch gate prevents dispatching for dispatch-from-main runs
        assert "default_branch" in gate

    def test_retry_job_default_token_is_read_only(self, retry_workflow):
        # #1325 Option B: write scopes (actions, pull-requests) now live on the
        # minted App token, not the job's default GITHUB_TOKEN — mirrors
        # auto-merge-enable.yml's pattern. contents:read covers checkout.
        perms = retry_workflow["jobs"]["retry"]["permissions"]
        assert perms.get("contents") == "read"
        assert perms.get("actions") != "write", (
            "actions:write must come from the minted App token, not the "
            "default GITHUB_TOKEN — a GITHUB_TOKEN-attributed rerun is still "
            "a bot action and GitHub's recursion prevention suppresses the "
            "downstream workflow_run event it would otherwise produce (#1325 "
            "Finding 1)."
        )
        assert perms.get("pull-requests") != "write"

    def test_retry_job_mints_app_token(self, retry_workflow):
        # #1325 Option B: a GITHUB_TOKEN-attributed action is recursion-prevented
        # by GitHub — a retry dispatched with GITHUB_TOKEN never arms a further
        # workflow_run event if IT fails too, so a failed retry can't self-heal
        # (Finding 1). An App installation token is not GITHUB_TOKEN-attributed,
        # so the downstream event fires normally. Same fix as auto-merge-enable.yml
        # / merge-train.yml (decision 2ccfa29a).
        steps = retry_workflow["jobs"]["retry"]["steps"]
        names = [s.get("name", "") for s in steps]
        assert any("Mint" in n and "token" in n for n in names), (
            "Retry job must mint a GitHub App installation token"
        )
        mint_step = next(
            s for s in steps if "Mint" in s.get("name", "") and "token" in s.get("name", "")
        )
        assert "create-github-app-token" in mint_step.get("uses", "")
        assert mint_step["with"].get("permission-actions") == "write"
        assert mint_step["with"].get("permission-pull-requests") == "write"

    def test_retry_job_uses_app_token_not_github_token(self, retry_workflow):
        steps = retry_workflow["jobs"]["retry"]["steps"]
        invoke_step = next(s for s in steps if "code_review_retry.py" in s.get("run", ""))
        gh_token = invoke_step["env"].get("GH_TOKEN", "")
        assert "secrets.GITHUB_TOKEN" not in gh_token
        assert "app-token" in gh_token or "steps." in gh_token

    def test_retry_job_invokes_script(self, retry_workflow):
        steps = retry_workflow["jobs"]["retry"]["steps"]
        invocations = " ".join(s.get("run", "") for s in steps)
        assert "code_review_retry.py" in invocations

    def test_retry_job_passes_required_env(self, retry_workflow):
        steps = retry_workflow["jobs"]["retry"]["steps"]
        invoke_step = next(s for s in steps if "code_review_retry.py" in s.get("run", ""))
        env = invoke_step["env"]
        # The script reads these env vars (see scripts/code_review_retry.py:main).
        for key in ("REPO", "HEAD_BRANCH", "HEAD_SHA", "FAILED_RUN_ID", "RUN_ATTEMPT", "GH_TOKEN"):
            assert key in env, f"Retry step must pass {key} env to the script"

    def test_run_attempt_sourced_from_workflow_run_event(self, retry_workflow):
        # #1325: must come from the webhook payload's run_attempt, not a
        # runs-list count (gh run rerun increments run_attempt on the SAME
        # run row, so a fresh-count approach would never see it move).
        steps = retry_workflow["jobs"]["retry"]["steps"]
        invoke_step = next(s for s in steps if "code_review_retry.py" in s.get("run", ""))
        assert invoke_step["env"]["RUN_ATTEMPT"] == "${{ github.event.workflow_run.run_attempt }}"


class TestReviewWorkflowUntouched:
    """The split-out retry workflow must NOT modify code-review.yml triggers."""

    def test_review_workflow_keeps_pull_request_trigger(self, review_workflow):
        on = _triggers(review_workflow)
        assert "pull_request" in on
        assert "workflow_dispatch" in on

    def test_review_workflow_has_no_workflow_run_trigger(self, review_workflow):
        # If this trigger reappears, we're back to the validation-failure
        # scenario where GitHub rejects self-referencing workflow_run.
        on = _triggers(review_workflow)
        assert "workflow_run" not in on, (
            "code-review.yml must not self-reference via workflow_run — "
            "use code-review-retry.yml instead."
        )
