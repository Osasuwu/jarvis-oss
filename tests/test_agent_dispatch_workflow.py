"""Contract checks for .github/workflows/agent-dispatch.yml.

Text-level on purpose (no YAML dependency): each check pins one property that
keeps the unattended worker from reaching past its own branch and PR.
"""

import re
from pathlib import Path

WORKFLOW = (
    Path(__file__).parent.parent / ".github" / "workflows" / "agent-dispatch.yml"
).read_text(encoding="utf-8")


def _tool_list(flag: str) -> str:
    match = re.search(rf"{flag} (.+)", WORKFLOW)
    assert match, f"{flag} missing"
    return match.group(1)


def test_triggers_only_on_issue_labeled_for_the_dispatch_label():
    assert re.search(r"on:\n  issues:\n    types: \[labeled\]\n", WORKFLOW)
    assert "pull_request" not in WORKFLOW.split("jobs:")[0]
    assert "if: github.event.label.name == 'agent:dispatch'" in WORKFLOW


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_no_merge_path_exists():
    # Neither the model's shell nor any deterministic step may merge.
    assert "gh pr merge" not in _tool_list("--allowed-tools")
    run_blocks = "\n".join(re.findall(r"run: \|\n((?:          .*\n?)+)", WORKFLOW))
    assert run_blocks
    assert "gh pr merge" not in run_blocks
    assert "mergePullRequest" not in run_blocks
    assert "--auto" not in run_blocks


def test_worker_cannot_touch_labels_protection_or_the_raw_api():
    allowed = _tool_list("--allowed-tools")
    for forbidden in ("gh issue edit", "gh pr edit", "gh api", "gh pr merge"):
        assert forbidden not in allowed, forbidden
    denied = _tool_list("--disallowed-tools")
    for forbidden in ("gh issue edit", "gh pr edit", "gh api", "gh pr merge"):
        assert f"Bash({forbidden}:*)" in denied, forbidden


def test_worker_cannot_edit_its_own_execution_environment():
    denied = _tool_list("--disallowed-tools")
    for path in ("./.github/workflows/**", "./.agents/hooks/**", "./.claude/**"):
        assert f"Edit({path})" in denied, path
        assert f"Write({path})" in denied, path


def test_history_rewrites_are_denied():
    denied = _tool_list("--disallowed-tools")
    for cmd in (
        "git push --force",
        "git push -f",
        "git push --force-with-lease",
        "git commit --amend",
        "git reset --hard",
    ):
        assert f"Bash({cmd}:*)" in denied, cmd


def test_worker_pushes_with_a_pat_so_required_checks_run():
    worker = WORKFLOW.split("name: Run unattended worker")[1].split("- name:")[0]
    assert "github_token: ${{ secrets.AGENT_DISPATCH_PAT }}" in worker


def test_run_without_a_pr_goes_to_owner_queue():
    step = WORKFLOW.split("name: Route to owner queue when no PR was opened")[1]
    assert "if: ${{ !cancelled() }}" in step
    assert '--add-label "status:owner-queue"' in step
    # Exact-or-dash prefix match: issue 10 must not claim issue 100's branch.
    assert 'index($1, p "-") == 1' in step


def test_worker_timeout_fails_the_step_instead_of_cancelling_the_job():
    # A job-level timeout cancels, which would skip the `!cancelled()`
    # owner-queue step; the worker step must time out first.
    job = int(re.search(r"^    timeout-minutes: (\d+)", WORKFLOW, re.M).group(1))
    worker = WORKFLOW.split("name: Run unattended worker")[1].split("- name:")[0]
    step = int(re.search(r"timeout-minutes: (\d+)", worker).group(1))
    assert step < job
