"""Drift guard for the code-review action's `--allowed-tools` allowlist.

The code-review action runs HEADLESS (`anthropics/claude-code-action@v1`): any
tool the plugin's reviewer agents invoke that is NOT in `--allowed-tools` is
DENIED outright — there is no human to approve the prompt. When the allowlist
is a strict subset of what the plugin actually uses, the denied calls turn into
repeated `permission_denials`: the agents burn turns retrying, then flail at the
final comment-post step — posting `test`/`PLACEHOLDER`/`ping` probe comments,
fragmenting the review across comments, or posting nothing. A missing or
unparseable verdict comment fails the merge gate CLOSED (#993), and the PR ends
up admin-merged. (jarvis#1042; incident `incident_pr963_rework_blowup`.)

Concretely: plugin reviewer agent #9 (structural-growth) runs
`git show <sha>:<file> | wc -l`, and agent #3 reads `git blame` / `git log`.
Those four tools (`git show`, `git blame`, `git log`, `wc`) were absent from the
allowlist for months, producing ~9 denials per run.

This is the #326 silent-subset-drift class: nothing compared the workflow
allowlist against the tools the plugin needs, so the gap was invisible. This
guard pins the load-bearing tools in BOTH the live reference workflow and the
repo-baseline canon (the propagation template pushed to every owned repo,
including redrobot) so the fix can't silently regress.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"
CANON_WORKFLOW = REPO_ROOT / "scripts" / "repo_baseline" / "canon" / "code-review.yml"

# The tools whose absence caused the #1042 permission_denials. These are the
# git/structural tools the plugin's reviewer agents invoke; if any is dropped
# from the allowlist the headless action denies it and the post step degrades.
REQUIRED_TOOLS = (
    "Bash(git show:*)",
    "Bash(git blame:*)",
    "Bash(git log:*)",
    "Bash(wc:*)",
    # #971: the plugin composes the verdict body with the Write tool at
    # /tmp/code-review-comment.md and posts via `gh pr comment --body-file`,
    # so no shell string-interpretation touches review prose (backticks,
    # $(...), $VAR would otherwise be evaluated under bash -c). Dropping this
    # grant denies the Write in the headless runner and the post step degrades
    # back to shell-assembled bodies.
    "Write(//tmp/**)",
)

# Sanity floor — the pre-existing tools that must never disappear either.
BASELINE_TOOLS = (
    "Bash(gh pr comment:*)",
    "Bash(gh pr diff:*)",
    "Bash(gh pr view:*)",
)

_ALLOWED_TOOLS_RE = re.compile(r'--allowed-tools\s+"([^"]*)"')


def _allowed_tools_blocks(path: Path) -> list[str]:
    """Every `--allowed-tools "..."` string in the file (canon has two jobs)."""
    text = path.read_text(encoding="utf-8")
    blocks = _ALLOWED_TOOLS_RE.findall(text)
    assert blocks, f"no --allowed-tools line found in {path}"
    return blocks


@pytest.mark.parametrize("path", [LIVE_WORKFLOW, CANON_WORKFLOW], ids=["live", "canon"])
def test_required_git_tools_present(path: Path) -> None:
    for block in _allowed_tools_blocks(path):
        for tool in REQUIRED_TOOLS:
            assert tool in block, (
                f"{path.name}: allowlist missing {tool!r} — headless action will "
                f"DENY it, causing permission_denials and degraded post step "
                f"(jarvis#1042). Allowlist was: {block}"
            )


@pytest.mark.parametrize("path", [LIVE_WORKFLOW, CANON_WORKFLOW], ids=["live", "canon"])
def test_baseline_tools_present(path: Path) -> None:
    for block in _allowed_tools_blocks(path):
        for tool in BASELINE_TOOLS:
            assert tool in block, f"{path.name}: allowlist dropped baseline tool {tool!r}"


def test_canon_jobs_share_one_allowlist() -> None:
    """Canon's two retry jobs (attempt-1/attempt-2) must carry identical lists —
    a fix applied to one job but not the other still leaks denials on retry."""
    blocks = _allowed_tools_blocks(CANON_WORKFLOW)
    assert len(blocks) == 2, f"expected 2 allowlist blocks in canon, got {len(blocks)}"
    assert blocks[0] == blocks[1], "canon attempt-1 and attempt-2 allowlists diverged"


def test_live_and_canon_allowlists_match() -> None:
    """Live reference and canon template must agree, or propagated repos get a
    different (stale) allowlist than the one we validated here."""
    live = _allowed_tools_blocks(LIVE_WORKFLOW)[0]
    canon = _allowed_tools_blocks(CANON_WORKFLOW)[0]
    assert live == canon, (
        "live workflow and canon allowlists diverged — re-snapshot the canon "
        "after changing the live allowlist so the fix propagates to owned repos"
    )
