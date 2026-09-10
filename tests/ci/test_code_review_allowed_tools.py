"""Drift guard for the code-review action's `--allowed-tools` allowlist.

The code-review action runs HEADLESS (`anthropics/claude-code-action@v1`): any
tool the reviewer invokes that is NOT in `--allowed-tools` is DENIED outright —
there is no human to approve the prompt. When the allowlist is a strict subset
of what the reviewer actually uses, the denied calls turn into repeated
`permission_denials`: the agent burns turns retrying, then flails at the final
comment-post step — posting `test`/`PLACEHOLDER`/`ping` probe comments,
fragmenting the review across comments, or posting nothing. A missing or
unparseable verdict comment fails the merge gate CLOSED (#993), and the PR ends
up admin-merged. (jarvis#1042; incident `incident_pr963_rework_blowup`.)

This is the #326 silent-subset-drift class: nothing compared the workflow
allowlist against the tools the reviewer needs, so the gap was invisible. This
guard pins the load-bearing tools in the live reference workflow so the
fix can't silently regress.

#1816: the code-review plugin invocation was retired in favor of a direct
single-pass prompt (Layer B of the code-gate rebuild). The plugin-prose ⇄
allowlist diff suite that used to pin the vendored plugin command snapshot
(jarvis#1225) is retired along with it — there is no more vendored prose to
diff against a live allowlist. `Skill(code-review:code-review)` is dropped
from REQUIRED_TOOLS since the reviewer no longer dispatches through a plugin
Skill invocation.

#1850 DENYLIST REBUILD: the narrow per-verb allowlist model itself was the
recurring root cause (jarvis#1042, #1198, #1210, #1218, #1223, #1841 — five
rounds of "reviewer needed an unenumerated read verb, got denied, patch in
the one missing prefix"). The allowlist now grants `Bash(git:*)` and the `gh`
noun-groups the reviewer uses (`gh pr:*`, `gh issue:*`, `gh search:*`,
`gh label:*`) wholesale, with a `--disallowed-tools` list carving the
mutating verbs back out — mirrors the allow+disallow pattern already shipped
in agent-dispatch.yml. `REQUIRED_TOOLS` entries that named individual git
read verbs (`git show`/`git blame`/`git log`/`git fetch`) are superseded by
the wholesale `Bash(git:*)` grant; this guard now also pins the
`--disallowed-tools` mutating-verb list so the denylist can't silently thin
out the same way the old allowlist did.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "code-review.yml"

# The tools whose absence caused the #1042 permission_denials. These are the
# git/structural tools the reviewer invokes; if any is dropped from the
# allowlist the headless action denies it and the post step degrades.
#
# #1850: individual git read-verb entries (`git show`/`git blame`/`git log`/
# `git fetch`) were retired from this list — they're superseded by the
# wholesale `Bash(git:*)` grant the denylist rebuild introduced. Pinning them
# individually would just re-create the whack-a-mole this rebuild exists to
# end; `test_git_wholesale_grant_present` below pins the wholesale grant
# instead.
REQUIRED_TOOLS = (
    # Native file-reading tools — the reviewer prose steers it to Read/Grep/
    # Glob instead of Bash `cat`/`grep`/`find`. Dropping any re-opens the
    # allowlist-drift class (`code_review_allowlist_drift_class`).
    "Read",
    "Grep",
    "Glob",
    "Bash(wc:*)",
    # #1841: merge-commit second-parent inspection (an autobase-pushed
    # "Merge branch 'main' into <pr-branch>" commit) needs these to look up
    # the merge commit and diff its parents. `gh api` stays on its own narrow
    # grant even after #1850 — its `-X <method>`/`--input` mutation flags can
    # appear anywhere in the command line, so a prefix-matched disallow can't
    # reliably catch every spelling the way it can for `gh pr`/`gh issue`.
    "Bash(gh api repos/*/commits/*:*)",
    "Bash(gh api repos/*/compare/*:*)",
    # Compound-command guard: headless permission matching splits on ; | && and
    # newlines and checks each sub-command, so an un-allowlisted `echo` prefix
    # (`echo "=== …" ; gh pr view …`) denies the whole compound even though
    # `gh pr view` is allowlisted. This was an observed denial on PR #1226.
    "Bash(echo:*)",
    # #971: the reviewer composes the verdict body with the Write tool at
    # /tmp/code-review-comment.md and posts via `gh pr comment --body-file`,
    # so no shell string-interpretation touches review prose (backticks,
    # $(...), $VAR would otherwise be evaluated under bash -c). Dropping this
    # grant denies the Write in the headless runner and the post step degrades
    # back to shell-assembled bodies. Granted UNSCOPED (`Write`, not
    # `Write(//tmp/**)`): the `//tmp/**` glob failed to match `/tmp/...` on
    # the Linux runner, denying the verdict Write.
    "Write",
)

# #1850: the wholesale grants the denylist rebuild introduced. These replace
# the old per-verb git/gh entries — dropping any of these re-opens exactly
# the allowlist-drift class this rebuild was meant to close.
WHOLESALE_GRANTS = (
    "Bash(git:*)",
    "Bash(gh pr:*)",
    "Bash(gh issue:*)",
    "Bash(gh search:*)",
    "Bash(gh label:*)",
)

# #1850: the mutating verbs in the wholesale-granted noun-groups above must
# stay carved out via --disallowed-tools, or the wholesale grants turn into a
# real mutation surface (this job's token is pull-requests: write, so a `gh
# pr edit`/`gh issue close` slipping through would actually succeed against
# the API, unlike git push which the read-only contents token can't do
# regardless).
REQUIRED_DISALLOWED = (
    "Bash(gh pr merge:*)",
    "Bash(gh pr close:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr reopen:*)",
    "Bash(gh pr review:*)",
    "Bash(gh pr ready:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr lock:*)",
    "Bash(gh pr unlock:*)",
    "Bash(gh issue create:*)",
    "Bash(gh issue edit:*)",
    "Bash(gh issue close:*)",
    "Bash(gh issue reopen:*)",
    "Bash(gh issue delete:*)",
    "Bash(gh issue lock:*)",
    "Bash(gh issue unlock:*)",
    "Bash(gh issue pin:*)",
    "Bash(gh issue unpin:*)",
    "Bash(gh issue transfer:*)",
    "Bash(gh issue comment:*)",
    "Bash(gh label create:*)",
    "Bash(gh label edit:*)",
    "Bash(gh label delete:*)",
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(git merge:*)",
    "Bash(git reset:*)",
    "Bash(git rebase:*)",
    "Bash(git cherry-pick:*)",
    "Bash(git stash:*)",
    "Bash(git clean:*)",
    "Bash(git rm:*)",
    "Bash(git mv:*)",
    "Bash(git apply:*)",
    "Bash(git am:*)",
    "Bash(git checkout:*)",
    "Bash(git switch:*)",
    "Bash(git restore:*)",
)

# Sanity floor — the pre-existing tools that must never disappear. #1850:
# these three are individual `gh pr` verbs superseded by the wholesale
# `Bash(gh pr:*)` grant tested in `test_wholesale_git_and_gh_grants_present`
# (that grant subsumes them, so pinning them as literal substrings would
# just fail against the new broadened string). Kept as a comment rather than
# a dropped test: `gh pr view`/`diff`/`comment` are the reviewer's actual
# minimum viable command set — if the wholesale grant is ever narrowed back
# to individual verbs, these three are the floor to restore first.

_ALLOWED_TOOLS_RE = re.compile(r'--allowed-tools\s+"([^"]*)"')
_DISALLOWED_TOOLS_RE = re.compile(r'--disallowed-tools\s+((?:"[^"]*"\s*)+)')


def _allowed_tools_blocks(path: Path) -> list[str]:
    """Every `--allowed-tools "..."` string in the file."""
    text = path.read_text(encoding="utf-8")
    blocks = _ALLOWED_TOOLS_RE.findall(text)
    assert blocks, f"no --allowed-tools line found in {path}"
    return blocks


def _disallowed_tools_blocks(path: Path) -> list[list[str]]:
    """Every `--disallowed-tools "a" "b" ...` entry list in the file."""
    text = path.read_text(encoding="utf-8")
    raw_blocks = _DISALLOWED_TOOLS_RE.findall(text)
    assert raw_blocks, f"no --disallowed-tools line found in {path}"
    return [re.findall(r'"([^"]*)"', raw) for raw in raw_blocks]


@pytest.mark.parametrize("path", [LIVE_WORKFLOW], ids=["live"])
def test_required_git_tools_present(path: Path) -> None:
    for block in _allowed_tools_blocks(path):
        for tool in REQUIRED_TOOLS:
            assert tool in block, (
                f"{path.name}: allowlist missing {tool!r} — headless action will "
                f"DENY it, causing permission_denials and degraded post step "
                f"(jarvis#1042). Allowlist was: {block}"
            )


@pytest.mark.parametrize("path", [LIVE_WORKFLOW], ids=["live"])
def test_wholesale_git_and_gh_grants_present(path: Path) -> None:
    """#1850: the denylist rebuild's core grants — dropping any of these
    re-opens the per-verb whack-a-mole the rebuild was meant to end."""
    for block in _allowed_tools_blocks(path):
        for tool in WHOLESALE_GRANTS:
            assert tool in block, (
                f"{path.name}: allowlist missing wholesale grant {tool!r} — this is "
                f"the #1850 denylist-rebuild grant, dropping it reopens the "
                f"allowlist-drift class. Allowlist was: {block}"
            )


@pytest.mark.parametrize("path", [LIVE_WORKFLOW], ids=["live"])
def test_mutating_verbs_disallowed(path: Path) -> None:
    """#1850: the wholesale `Bash(git:*)`/`Bash(gh pr:*)`/`Bash(gh issue:*)`/
    `Bash(gh label:*)` grants above are only safe as long as every mutating
    verb in those noun-groups is carved back out via --disallowed-tools."""
    for block in _disallowed_tools_blocks(path):
        for tool in REQUIRED_DISALLOWED:
            assert tool in block, (
                f"{path.name}: --disallowed-tools missing {tool!r} — the wholesale "
                f"#1850 allow grants make this a real mutation surface without the "
                f"disallow entry. Disallowed-tools was: {block}"
            )


def test_plugin_skill_grant_retired() -> None:
    """#1816: the plugin invocation is gone — `Skill(code-review:code-review)`
    must not reappear in the allowlist (it would be a dead/unreachable grant,
    or a regression back toward the plugin)."""
    for block in _allowed_tools_blocks(LIVE_WORKFLOW):
        assert "Skill(code-review:code-review)" not in block, (
            "allowlist still grants the retired plugin Skill invocation "
            f"(#1816 dropped the plugin). Allowlist was: {block}"
        )


def test_plugin_marketplace_inputs_retired() -> None:
    """#1816: `plugins:`/`plugin_marketplaces:` inputs to the review action
    must not reappear — the reviewer is a direct prompt now, not a plugin."""
    text = LIVE_WORKFLOW.read_text(encoding="utf-8")
    assert "plugins: code-review@jarvis-fork-plugins" not in text
    assert "plugin_marketplaces:" not in text
