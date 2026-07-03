"""Meta-test for .github/workflows/auto-merge-enable.yml.

Reimplements the auto-merge-enable's per-PR enable decision in Python and
asserts it enables/skips the PRs the workflow promises, plus a config dimension
that keeps the YAML and this test in lockstep.

Why this test exists (CLAUDE.md §326 + #948 Bug A): auto-merge-enable.yml is the
fix for #948 Bug A — a PR auto-merged with the default GITHUB_TOKEN is attributed
to github-actions[bot], and GitHub's recursion-prevention then SUPPRESSES native
linked-issue auto-close (the merged PR leaves its `Closes #N` issue open). The fix
is to enable auto-merge with a GitHub App installation token so the merge's
`enabledBy` actor is the App, not the bot. That correctness hinges entirely on
config the diff can silently regress:
  - reverting the App token back to GITHUB_TOKEN reintroduces the exact bug,
  - dropping the draft/fork guard hard-fails fork PRs or enrols held drafts,
  - dropping the empty-output guard silently leaves a PR un-enrolled,
  - dropping cancel-in-progress:false can kill a run mid-enable.
None of those produce a red signal on their own — this test is that signal.

Enable rule mirrored here (see the `if:` guard + bash step in the YAML):
  eligible   = non-draft AND head repo == base repo (not a fork)
  enable     = eligible AND auto-merge not already enabled (autoMergeRequest null)
  skip       = eligible AND auto-merge already enabled (idempotent re-trigger)
  fail-loud  = eligible AND `gh pr view` returned empty (auth/API degradation)
"""

from __future__ import annotations

from pathlib import Path

import pytest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "auto-merge-enable.yml"
)


# ---- logic dimension --------------------------------------------------------
#
# The decision has three terminal states: ENABLE, SKIP, FAIL. `am` is the
# auto-merge token the workflow's jq emits: "null" when unset, "set" when
# already enabled, and "" only on degraded (whitespace-only) API output. The
# workflow reduces the raw autoMergeRequest object to this bare token via @tsv
# so `read` can't mis-split on spaces inside commitHeadline.


def decide(*, draft: bool, head_repo: str, base_repo: str, am: str, state: str = "OPEN") -> str:
    """Mirror the workflow's enable decision. Returns ENABLE / SKIP / FAIL / NOOP."""
    # `if:` guard — drafts and forks never reach the steps.
    if draft:
        return "NOOP"
    if head_repo != base_repo:
        return "NOOP"
    # Empty-output guard — fail loud rather than misread as "already enabled".
    if am == "" or state == "":
        return "FAIL"
    # cancel-in-progress:false lets a run queued near merge time execute after the
    # PR is already merged; on a merged PR am reads "null" but state is not OPEN.
    if state != "OPEN":
        return "NOOP"
    if am == "null":
        return "ENABLE"
    return "SKIP"


def test_ready_non_fork_with_no_automerge_enables():
    assert decide(draft=False, head_repo="o/r", base_repo="o/r", am="null") == "ENABLE"


def test_already_enabled_is_idempotent_skip():
    assert decide(draft=False, head_repo="o/r", base_repo="o/r", am="set") == "SKIP"


def test_draft_is_noop():
    assert decide(draft=True, head_repo="o/r", base_repo="o/r", am="null") == "NOOP"


def test_fork_pr_is_noop():
    assert decide(draft=False, head_repo="fork/r", base_repo="o/r", am="null") == "NOOP"


def test_empty_output_fails_loud():
    assert decide(draft=False, head_repo="o/r", base_repo="o/r", am="") == "FAIL"


def test_empty_state_fails_loud():
    assert decide(draft=False, head_repo="o/r", base_repo="o/r", am="null", state="") == "FAIL"


def test_already_merged_pr_is_noop():
    # Queued run executes after merge: am=="null" (auto-merge completed) but the
    # PR is no longer OPEN — must NOT call `gh pr merge` on a closed PR.
    assert decide(draft=False, head_repo="o/r", base_repo="o/r", am="null", state="MERGED") == "NOOP"


def test_closed_pr_is_noop():
    assert decide(draft=False, head_repo="o/r", base_repo="o/r", am="null", state="CLOSED") == "NOOP"


def test_fork_precedes_empty_output_fail():
    # Fork guard is evaluated before the empty-output FAIL path: a fork PR with
    # degraded output must NOOP (the `if:` skips the steps entirely), not FAIL.
    # Replaces a draft+fork case that duplicated the draft-only path (#1006 review,
    # NIT) — this one actually reaches the fork branch with am="".
    assert decide(draft=False, head_repo="fork/r", base_repo="o/r", am="") == "NOOP"


# ---- config dimension (keep YAML and test in lockstep) ----------------------


def test_workflow_exists():
    assert WORKFLOW_PATH.is_file(), "auto-merge-enable.yml missing"


def test_workflow_mints_app_token():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "create-github-app-token" in text, (
        "auto-merge-enable must mint a GitHub App token — auto-merge enabled with "
        "GITHUB_TOKEN attributes the merge to github-actions[bot], which suppresses "
        "native linked-issue auto-close (#948 Bug A)."
    )


def test_workflow_uses_app_token_not_github_token():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Minting the App token is not enough: Bug A reappears if GH_TOKEN on the
    # `gh pr merge` step reverts to secrets.GITHUB_TOKEN while the mint step stays
    # (token minted then discarded). Pin the *usage*, not just the presence.
    assert "steps.app-token.outputs.token" in text, (
        "GH_TOKEN for `gh pr merge --auto` must use the minted App token output "
        "(steps.app-token.outputs.token), not GITHUB_TOKEN — reverting is #948 Bug A."
    )
    # Match the regression VECTOR (the interpolated expression), not the bare
    # substring: a comment mentioning secrets.GITHUB_TOKEN would false-positive
    # the broad check (#1006 review, MINOR).
    assert "${{ secrets.GITHUB_TOKEN }}" not in text, (
        "auto-merge-enable must never pass ${{ secrets.GITHUB_TOKEN }} to gh — it "
        "would re-attribute the merge to github-actions[bot] and suppress auto-close."
    )


def test_workflow_app_token_is_sha_pinned():
    import re

    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Supply-chain hardening: the action must be pinned to a full 40-hex commit
    # SHA, not a mutable tag (@v3). Match the SHANESS, not the exact commit, so a
    # Dependabot pin bump doesn't red this test (the sibling merge-train guard
    # follows the same intent-not-literal pattern).
    assert re.search(r"create-github-app-token@[0-9a-f]{40}", text), (
        "create-github-app-token must be SHA-pinned (supply-chain), not tag-pinned."
    )


def test_workflow_scopes_token_to_this_repo():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Least-privilege: the App is shared across repos (jarvis + redrobot), so the
    # minted token must be scoped to this repo only. Derived from GITHUB_REPOSITORY
    # so it's populated on every trigger.
    assert "REPO_NAME=${GITHUB_REPOSITORY#*/}" in text, (
        "token must be scoped via env REPO_NAME derived from GITHUB_REPOSITORY."
    )
    assert "repositories: ${{ env.REPO_NAME }}" in text, (
        "create-github-app-token must pass repositories: env.REPO_NAME for least-privilege."
    )


def test_workflow_guards_drafts_and_forks():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "github.event.pull_request.draft == false" in text, (
        "draft guard dropped — held drafts would get auto-merge enabled."
    )
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text, (
        "fork guard dropped — fork PRs (no secrets) would hard-fail the token step."
    )


def test_workflow_has_empty_output_guard():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # The guard is compound: `[ -z "$am" ] || [ -z "$state" ]`. Assert BOTH halves
    # (#1006 review, MINOR) — dropping the `$state` half would leave the
    # state-emptiness path unguarded while this test still passed on the `$am` half.
    assert '[ -z "$am" ]' in text, (
        "empty-output guard dropped — a degraded `gh pr view` would be misread as "
        "'already enabled', silently leaving the PR un-enrolled."
    )
    assert '[ -z "$state" ]' in text, (
        "state empty-guard half dropped — a degraded `gh pr view` could leave "
        "$state empty and the merged-PR guard would misfire."
    )


def test_workflow_guards_empty_app_token():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # If the Mint step is skipped (REPO_NAME empty), GH_TOKEN is "" and gh could
    # fall back to the ambient GITHUB_TOKEN — re-attributing the merge to the bot
    # (#948 Bug A). The Enable step must fail loud on an empty token before any gh
    # call (#1006 review, MAJOR).
    assert '[ -z "${GH_TOKEN:-}" ]' in text, (
        "Enable step must fail loud when the App token is empty — an empty GH_TOKEN "
        "risks a GITHUB_TOKEN fallback and a bot-attributed merge (#948 Bug A)."
    )


def test_workflow_enables_only_when_unset():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '[ "$am" = "null" ]' in text, (
        "idempotency check dropped — re-triggers would re-call `gh pr merge --auto` "
        "and surface a spurious failure."
    )


def test_workflow_emits_clean_automerge_token():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # The raw autoMergeRequest object carries a commitHeadline that can contain
    # spaces; interpolating it into `read -r am state` mis-splits state. The jq
    # must reduce it to a bare "null"/"set" token and emit with @tsv so `read`
    # splits only on the tab. Regressing to `\(.autoMergeRequest)` re-opens the
    # whitespace bug (#948 review round 6).
    assert 'if .autoMergeRequest == null then "null" else "set" end' in text, (
        "auto-merge status must be reduced to a bare null/set token, not the raw "
        "autoMergeRequest object (whitespace in commitHeadline mis-splits `read`)."
    )
    assert "| @tsv" in text, (
        "the read inputs must be tab-separated (@tsv) so `read` can't mis-split "
        "on spaces inside a field."
    )


def test_workflow_skips_non_open_pr():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # cancel-in-progress:false allows a run to land after the PR merged; the state
    # guard prevents calling `gh pr merge` on a closed PR (spurious red).
    assert '[ "$state" != "OPEN" ]' in text, (
        "merged-PR guard dropped — a queued run after merge would call "
        "`gh pr merge` on a closed PR and surface a spurious failure."
    )


def test_workflow_triggers_on_ready_for_review_and_reopened():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Dropping ready_for_review → draft→ready PRs never enrol; dropping reopened →
    # a reopened sandcastle never re-enrols. Both are silent failures.
    for trigger in ("opened", "ready_for_review", "reopened"):
        assert trigger in text, f"auto-merge-enable must trigger on {trigger!r}."


def test_workflow_repo_name_guard_is_load_bearing():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # An empty REPO_NAME makes create-github-app-token fall back to ALL installed
    # repos, leaking Issues:write to redrobot. The guard must fail loud instead.
    assert "GITHUB_REPOSITORY:?" in text, (
        "the REPO_NAME derivation must guard against an empty GITHUB_REPOSITORY "
        "(empty scope re-opens the cross-repo token leak)."
    )


def test_workflow_has_concurrency_guard():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "concurrency:" in text, (
        "auto-merge-enable must declare a concurrency group to serialize per-PR runs."
    )
    assert "cancel-in-progress: false" in text, (
        "auto-merge-enable must not cancel an in-flight run mid-enable — a cancel "
        "after token mint but before `gh pr merge` leaves the PR un-enrolled."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
