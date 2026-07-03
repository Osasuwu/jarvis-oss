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

Env contract:
  GH_TOKEN       — gh CLI auth (provided by Actions)
  REPO           — owner/name
  HEAD_BRANCH    — branch of the failed run (from workflow_run event)
  HEAD_SHA       — head SHA of the failed run
  FAILED_RUN_ID  — id of the failed run (used to fetch log + link in comments)

The pure functions (`decide`, `parse_reset_time_utc`, `count_failed_attempts`)
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
from datetime import datetime, timedelta, timezone
from typing import Iterable

MAX_ATTEMPTS = 4  # counts the triggering run; net retries = MAX_ATTEMPTS - 1 = 3
RESET_BUFFER_SEC = 60
MAX_SLEEP_SEC = 6 * 60 * 60  # 6h — Claude Max rolling window is ~5h, leave headroom
FAILED_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required"})

# Pattern observed in claude-code-action SDK failures, e.g.
# "Claude Code returned an error result: You've hit your session limit · resets 3:40am (UTC)"
_RESET_RE = re.compile(
    r"session limit\s*[·\-]\s*resets\s+(\d{1,2}):(\d{2})\s*(am|pm)?\s*\(UTC\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Decision:
    kind: str  # "dispatch" | "skip" | "exhausted"
    reason: str
    pr_number: int | None = None


def count_failed_attempts(runs: Iterable[dict]) -> int:
    return sum(1 for r in runs if r.get("conclusion") in FAILED_CONCLUSIONS)


def decide(
    open_prs: list[dict],
    head_branch: str,
    runs_for_sha: list[dict],
    max_attempts: int = MAX_ATTEMPTS,
) -> Decision:
    pr = next((p for p in open_prs if p.get("headRefName") == head_branch), None)
    if pr is None:
        return Decision("skip", f"no open PR for branch {head_branch}")
    fails = count_failed_attempts(runs_for_sha)
    if fails >= max_attempts:
        return Decision(
            "exhausted",
            f"{fails} failed attempts >= cap {max_attempts}",
            pr_number=pr["number"],
        )
    return Decision(
        "dispatch",
        f"failed attempt {fails + 1}/{max_attempts}",
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


def _dispatch(repo: str, branch: str, pr_number: int) -> None:
    subprocess.check_call([
        "gh", "workflow", "run", "code-review.yml",
        "--repo", repo, "--ref", branch,
        "-f", f"pr_number={pr_number}",
    ])


def _post_exhausted_comment(repo: str, pr_number: int, head_sha: str, run_id: str) -> None:
    body = (
        f"WARNING: Claude code-review auto-retry exhausted after {MAX_ATTEMPTS} "
        f"failed attempts on `{head_sha[:8]}`.\n\n"
        f"Last failed run: https://github.com/{repo}/actions/runs/{run_id}\n\n"
        f"Re-dispatch manually once resolved:\n"
        f"```\n"
        f"gh workflow run code-review.yml --repo {repo} -f pr_number={pr_number}\n"
        f"```"
    )
    subprocess.check_call([
        "gh", "pr", "comment", str(pr_number),
        "--repo", repo, "--body", body,
    ])


def main() -> int:
    repo = os.environ["REPO"]
    head_branch = os.environ["HEAD_BRANCH"]
    head_sha = os.environ["HEAD_SHA"]
    failed_run_id = os.environ.get("FAILED_RUN_ID", "")

    open_prs = json.loads(_gh(
        "pr", "list", "--repo", repo, "--state", "open",
        "--head", head_branch,
        "--json", "number,headRefName,headRefOid", "--limit", "5",
    ))
    runs_resp = json.loads(_gh(
        "api",
        f"repos/{repo}/actions/workflows/code-review.yml/runs?head_sha={head_sha}&per_page=100",
    ))
    if "workflow_runs" not in runs_resp:
        print(f"ERROR: unexpected API response (no workflow_runs key): {list(runs_resp.keys())}")
        return 1
    runs = runs_resp["workflow_runs"]

    # Finding #4: GitHub API can lag a few seconds after a workflow_run event fires.
    # If the triggering run's conclusion is still null, wait once and re-fetch.
    if any(r.get("id") == int(failed_run_id) and r.get("conclusion") is None
           for r in runs if failed_run_id):
        print("head run conclusion is null — sleeping 5s for API propagation, then retrying")
        time.sleep(5)
        runs_resp2 = json.loads(_gh(
            "api",
            f"repos/{repo}/actions/workflows/code-review.yml/runs?head_sha={head_sha}&per_page=100",
        ))
        if "workflow_runs" not in runs_resp2:
            print(f"ERROR: unexpected API response on propagation-lag retry "
                  f"(no workflow_runs key): {list(runs_resp2.keys())}")
            return 1
        runs = runs_resp2["workflow_runs"]
        if any(r.get("id") == int(failed_run_id) and r.get("conclusion") is None
               for r in runs):
            print("head run conclusion still null after propagation-lag retry — count may undercount")

    decision = decide(open_prs, head_branch, runs)
    print(f"failed_run={failed_run_id} sha={head_sha[:8]} branch={head_branch}")
    print(f"decision={decision.kind} reason={decision.reason} pr={decision.pr_number}")

    if decision.kind == "skip":
        return 0
    if decision.kind == "exhausted":
        _post_exhausted_comment(repo, decision.pr_number, head_sha, failed_run_id)
        return 0

    # decision.kind == "dispatch"
    log_text = _fetch_failed_log(repo, failed_run_id) if failed_run_id else ""
    now = datetime.now(timezone.utc)
    reset_at = parse_reset_time_utc(log_text, now)

    if reset_at is not None:
        delay = (reset_at - now).total_seconds() + RESET_BUFFER_SEC
        if delay > MAX_SLEEP_SEC:
            print(f"quota reset at {reset_at.isoformat()} is {delay/60:.0f}min away "
                  f"(> cap {MAX_SLEEP_SEC/60:.0f}min) — marking exhausted")
            _post_exhausted_comment(repo, decision.pr_number, head_sha, failed_run_id)
            return 0
        if delay > 0:
            print(f"quota reset at {reset_at.isoformat()}; sleeping {delay:.0f}s "
                  f"({delay/60:.1f}min) before retry")
            time.sleep(delay)

    # Finding #2: double-dispatch guard runs before every _dispatch(), not only
    # after quota-sleep. GitHub sometimes delivers duplicate workflow_run events.
    # Also covers the immediate-retry path (reset_at is None / non-quota failure).
    runs_before_dispatch_resp = json.loads(_gh(
        "api",
        f"repos/{repo}/actions/workflows/code-review.yml/runs?head_sha={head_sha}&per_page=20",
    ))
    if "workflow_runs" not in runs_before_dispatch_resp:
        print(f"ERROR: unexpected API response before dispatch "
              f"(no workflow_runs key): {list(runs_before_dispatch_resp.keys())}")
        return 1
    runs_before_dispatch = runs_before_dispatch_resp["workflow_runs"]
    if any(r.get("conclusion") == "success" for r in runs_before_dispatch):
        print("pre-dispatch: success run already exists for this SHA — no dispatch")
        return 0
    if any(r.get("status") in ("queued", "in_progress") for r in runs_before_dispatch):
        print("pre-dispatch: a run is already queued/in_progress — no dispatch")
        return 0

    print(f"dispatching retry for PR #{decision.pr_number} at {head_branch}")
    _dispatch(repo, head_branch, decision.pr_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
