"""Reactive retry for `.github/workflows/code-review.yml` (#807).

Fires from the workflow's own `workflow_run` trigger when a run completes with
conclusion=failure. Re-dispatches the same PR's review, with two guards:

  1. **Retry cap** — skip after MAX_ATTEMPTS failed runs on the same head SHA.
     Prevents infinite loops on permanent failures (plugin bug, malformed PR).

  2. **Quota-aware delay** — Claude Max session limits include a reset time in
     the error message (`You've hit your session limit · resets 3:40am (UTC)`).
     If the failed run's log carries that signature, sleep until reset + 60s
     before re-dispatching. Otherwise retry immediately (other transient
     failures: runner setup, plugin hiccup, GitHub API blip).

Also logs a `failure_signature` classification (`classify_failure_signature`,
#1325 Option C') on every code path — quota-reset / permission-denied /
no-log-available / unknown. The mechanism has a historically 0% observed
success rate with no record of *why*; this is diagnostic only (does not
affect the dispatch decision) and exists to give a follow-up investigation
something to grep for instead of raw Action logs.

Env contract:
  GH_TOKEN       — gh CLI auth (provided by Actions; a GitHub App installation
                   token as of #1325 Option B — see code-review-retry.yml)
  REPO           — owner/name
  HEAD_BRANCH    — branch of the failed run (from workflow_run event)
  HEAD_SHA       — head SHA of the failed run
  FAILED_RUN_ID  — id of the failed run — reused as the rerun target (#1325
                   Option A: `gh run rerun <id> --failed` reruns this SAME
                   run in place instead of firing a fresh dispatch)
  RUN_ATTEMPT    — github.event.workflow_run.run_attempt; drives the retry
                   cap directly instead of counting runs-list rows (#1325)

The pure functions (`decide`, `parse_reset_time_utc`, `classify_failure_signature`)
are covered by `tests/ci/test_code_review_retry.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_ATTEMPTS = 4  # counts the triggering run; net retries = MAX_ATTEMPTS - 1 = 3
RESET_BUFFER_SEC = 60
MAX_SLEEP_SEC = 6 * 60 * 60  # 6h — Claude Max rolling window is ~5h, leave headroom

# Pattern observed in claude-code-action SDK failures, e.g.
# "Claude Code returned an error result: You've hit your session limit · resets 3:40am (UTC)"
_RESET_RE = re.compile(
    r"session limit\s*[·\-]\s*resets\s+(\d{1,2}):(\d{2})\s*(am|pm)?\s*\(UTC\)",
    re.IGNORECASE,
)

# #1325 Option C': the retry mechanism has a historically 0% observed success
# rate — before this, a failed retry left no trace of *why* a rerun didn't
# help. GitHub App / GITHUB_TOKEN permission errors surface in gh CLI /
# Actions logs in a couple of recognizable shapes; a rerun can't fix a
# permission error (Option B addresses the App-token half of that, but a
# misconfigured App installation would still show up here).
_PERMISSION_DENIED_RE = re.compile(
    r"resource not accessible by integration|bad credentials|HTTP 40[13]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Decision:
    kind: str  # "dispatch" | "skip" | "exhausted"
    reason: str
    pr_number: int | None = None


def decide(
    open_prs: list[dict],
    head_branch: str,
    head_sha: str,
    run_attempt: int,
    max_attempts: int = MAX_ATTEMPTS,
) -> Decision:
    pr = next((p for p in open_prs if p.get("headRefName") == head_branch), None)
    if pr is None:
        return Decision("skip", f"no open PR for branch {head_branch}")
    if pr.get("headRefOid") != head_sha:
        return Decision(
            "skip",
            f"PR head moved past {head_sha[:8]} (now {str(pr.get('headRefOid'))[:8]}) — stale retry",
            pr_number=pr["number"],
        )
    if run_attempt >= max_attempts:
        return Decision(
            "exhausted",
            f"run_attempt {run_attempt} >= cap {max_attempts}",
            pr_number=pr["number"],
        )
    return Decision(
        "dispatch",
        f"attempt {run_attempt}/{max_attempts}",
        pr_number=pr["number"],
    )


def parse_reset_time_utc(log_text: str, now: datetime) -> datetime | None:
    """Find the *next* quota-reset UTC datetime in a failed run's log, or None."""
    m = _RESET_RE.search(log_text)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm:
        ampm = ampm.lower()
        # am/pm only valid for 12-hour clock (1–12); 13:00am/pm is nonsense.
        if not (1 <= hour <= 12):
            return None
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        # Reset time has already passed (log-fetch latency or near-boundary).
        # Return None so the caller retries immediately rather than sleeping ~24h.
        return None
    return target


def classify_failure_signature(log_text: str) -> str:
    """Classify a failed run's log into a diagnostic bucket (#1325 Option C').

    Purely observational — does not affect the dispatch decision. Logged
    alongside `decision=...` so a run of failed retries builds up a signal on
    *why* the mechanism has historically shown a 0% observed success rate,
    without requiring manual log archaeology after the fact.
    """
    if not log_text:
        return "no-log-available"
    if _RESET_RE.search(log_text):
        return "quota-reset"
    if _PERMISSION_DENIED_RE.search(log_text):
        return "permission-denied"
    return "unknown"


def _gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def _fetch_failed_log(repo: str, run_id: str) -> str:
    try:
        return subprocess.check_output(
            ["gh", "run", "view", run_id, "--repo", repo, "--log-failed"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return ""


def _dispatch(repo: str, run_id: str) -> None:
    # #1325 Option A: rerun the SAME run in place instead of a fresh
    # `gh workflow run` dispatch. A fresh dispatch creates a new run (and a
    # new `review` check-run) alongside the earlier failing one, and the
    # stale failure is never cleared — mergeStateStatus stays BLOCKED even
    # after the retry succeeds (#1325 Finding 2). Rerunning in place updates
    # the one check-run's conclusion instead of shadowing it with a second.
    subprocess.check_call(
        [
            "gh",
            "run",
            "rerun",
            run_id,
            "--repo",
            repo,
            "--failed",
        ]
    )


def _post_exhausted_comment(repo: str, pr_number: int, head_sha: str, run_id: str) -> None:
    body = (
        f"WARNING: Claude code-review auto-retry exhausted after {MAX_ATTEMPTS} "
        f"failed attempts on `{head_sha[:8]}`.\n\n"
        f"Last failed run: https://github.com/{repo}/actions/runs/{run_id}\n\n"
        f"Re-run manually once resolved:\n"
        f"```\n"
        f"gh run rerun {run_id} --repo {repo} --failed\n"
        f"```"
    )
    subprocess.check_call(
        [
            "gh",
            "pr",
            "comment",
            str(pr_number),
            "--repo",
            repo,
            "--body",
            body,
        ]
    )


def main() -> int:
    repo = os.environ["REPO"]
    head_branch = os.environ["HEAD_BRANCH"]
    head_sha = os.environ["HEAD_SHA"]
    failed_run_id = os.environ.get("FAILED_RUN_ID", "")
    # #1325 Option A: run_attempt comes straight off the workflow_run webhook
    # payload (github.event.workflow_run.run_attempt), not a runs-list count.
    # `gh run rerun` increments run_attempt on the SAME run row, so a
    # count-the-runs-list approach silently stayed at 1 forever once dispatch
    # stopped creating new run rows — this is the direct fix for that.
    run_attempt = int(os.environ.get("RUN_ATTEMPT", "1"))

    open_prs = json.loads(
        _gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--head",
            head_branch,
            "--json",
            "number,headRefName,headRefOid",
            "--limit",
            "5",
        )
    )

    decision = decide(open_prs, head_branch, head_sha, run_attempt)
    print(
        f"failed_run={failed_run_id} sha={head_sha[:8]} branch={head_branch} attempt={run_attempt}"
    )
    print(f"decision={decision.kind} reason={decision.reason} pr={decision.pr_number}")

    # #1325 Option C': classify the failed run's log unconditionally, not only
    # on the dispatch path — skip/exhausted outcomes are data too. The retry
    # mechanism has a historically 0% observed success rate with no record of
    # *why*; this is the diagnostic trail that follow-up issue is meant to read.
    log_text = _fetch_failed_log(repo, failed_run_id) if failed_run_id else ""
    print(f"failure_signature={classify_failure_signature(log_text)}")

    if decision.kind == "skip":
        return 0
    if decision.kind == "exhausted":
        _post_exhausted_comment(repo, decision.pr_number, head_sha, failed_run_id)
        return 0

    # decision.kind == "dispatch"
    now = datetime.now(timezone.utc)
    reset_at = parse_reset_time_utc(log_text, now)

    if reset_at is not None:
        delay = (reset_at - now).total_seconds() + RESET_BUFFER_SEC
        if delay > MAX_SLEEP_SEC:
            print(
                f"quota reset at {reset_at.isoformat()} is {delay / 60:.0f}min away "
                f"(> cap {MAX_SLEEP_SEC / 60:.0f}min) — marking exhausted"
            )
            _post_exhausted_comment(repo, decision.pr_number, head_sha, failed_run_id)
            return 0
        if delay > 0:
            print(
                f"quota reset at {reset_at.isoformat()}; sleeping {delay:.0f}s "
                f"({delay / 60:.1f}min) before retry"
            )
            time.sleep(delay)

    # Finding #2: double-dispatch guard runs before every _dispatch(), not only
    # after quota-sleep. GitHub sometimes delivers duplicate workflow_run
    # events. Now targeted at the specific failed_run_id being rerun (rather
    # than a head_sha-wide runs-list scan) since #1325 Option A reruns that
    # exact run in place — a duplicate event racing this one would show up as
    # that same run already queued/in_progress/succeeded.
    if failed_run_id:
        run_state = json.loads(
            _gh(
                "run",
                "view",
                failed_run_id,
                "--repo",
                repo,
                "--json",
                "status,conclusion",
            )
        )
        if run_state.get("status") in ("queued", "in_progress"):
            print("pre-dispatch: run already queued/in_progress — no rerun")
            return 0
        if run_state.get("conclusion") == "success":
            print("pre-dispatch: run already succeeded — no rerun")
            return 0

    print(f"rerunning failed run {failed_run_id} for PR #{decision.pr_number} at {head_branch}")
    _dispatch(repo, failed_run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
