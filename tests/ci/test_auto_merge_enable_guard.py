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
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-merge-enable.yml"
)


# ---- logic dimension --------------------------------------------------------
#
# The decision has three terminal states: ENABLE, SKIP, FAIL. `am` is the
# auto-merge token the workflow's jq emits: "null" when unset, "set" when
# already enabled, and "" only on degraded (whitespace-only) API output. The
# workflow reduces the raw autoMergeRequest object to this bare token via @tsv
# so `read` can't mis-split on spaces inside commitHeadline.


def decide(
    *,
    draft: bool,
    head_repo: str,
    base_repo: str,
    am: str,
    state: str = "OPEN",
    code_review_diverged: bool = False,
) -> str:
    """Mirror the workflow's enable decision.

    Returns ENABLE / SKIP / FAIL / NOOP / WITHHOLD / DISARM.

    `code_review_diverged` is the carve-out dimension (#1234): True when the PR's
    `.github/workflows/code-review.yml` blob differs from the default branch's —
    a two-dot CONTENT comparison, so it covers both "this PR edits the file" and
    "this PR's branch is merely stale on it". Both trip claude-code-action's
    workflow validation identically.
    """
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
    # Carve-out (#1234): sits after the state/empty guards (a closed PR needs no
    # carve-out) but before the arm decision. Non-failure in both branches — an
    # expected-red check on a whole PR class is the noise this issue exists to
    # remove.
    if code_review_diverged:
        return "DISARM" if am == "set" else "WITHHOLD"
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
    assert (
        decide(draft=False, head_repo="o/r", base_repo="o/r", am="null", state="MERGED") == "NOOP"
    )


def test_closed_pr_is_noop():
    assert (
        decide(draft=False, head_repo="o/r", base_repo="o/r", am="null", state="CLOSED") == "NOOP"
    )


def test_code_review_divergence_withholds_automerge():
    # AC4/AC7 carve-out: when the PR's code-review.yml content differs from the
    # default branch's, claude-code-action skips on workflow validation and the
    # required `review` context goes green with ZERO review comments. Arming
    # auto-merge there would merge an unreviewed PR, so withhold instead.
    assert (
        decide(
            draft=False,
            head_repo="o/r",
            base_repo="o/r",
            am="null",
            code_review_diverged=True,
        )
        == "WITHHOLD"
    )


def test_code_review_divergence_disarms_already_armed_pr():
    # A PR can be armed BEFORE a code-review.yml change lands on main, which makes
    # it retroactively carve-out. Leaving it armed would let it merge on a green
    # but comment-less `review` context, so the carve-out disarms instead of
    # merely declining to arm.
    assert (
        decide(
            draft=False,
            head_repo="o/r",
            base_repo="o/r",
            am="set",
            code_review_diverged=True,
        )
        == "DISARM"
    )


def test_other_workflow_paths_still_enable():
    # AC7: the carve-out is scoped to code-review.yml specifically — a PR editing
    # pytest.yml (or any other workflow) reviews normally, so it must still arm.
    # claude-code-action validates its OWN calling workflow file, not all of
    # .github/workflows/**.
    assert (
        decide(
            draft=False,
            head_repo="o/r",
            base_repo="o/r",
            am="null",
            code_review_diverged=False,
        )
        == "ENABLE"
    )


def test_carve_out_does_not_apply_to_closed_pr():
    # The state guard precedes the carve-out: a merged PR needs no withholding.
    assert (
        decide(
            draft=False,
            head_repo="o/r",
            base_repo="o/r",
            am="null",
            state="MERGED",
            code_review_diverged=True,
        )
        == "NOOP"
    )


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


def test_workflow_requests_workflows_scope_explicitly():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # AC2 (#1234): without the workflows scope, `enablePullRequestAutoMerge` is
    # refused by GitHub on ANY PR touching .github/workflows/ ("refusing to allow
    # a GitHub App to create or update workflow ... without `workflows`
    # permission"), so every CI-editing PR silently degrades to manual merge.
    # Input name verified against actions/create-github-app-token@bcd2ba49 (v3.2.0)
    # action.yml, NOT guessed.
    assert "permission-workflows: write" in text, (
        "the minted App token must request the workflows scope explicitly, else "
        "auto-merge can never be armed on a .github/workflows/ PR (#1234)."
    )
    # Supplying ANY permission-* input REPLACES the installation's inherited
    # permissions with exactly the listed set (per the action's README). So the
    # scopes this workflow actually needs must be listed alongside, or the token
    # silently loses the ability to merge.
    assert "permission-contents: write" in text, (
        "down-scoping via permission-* drops inherited scopes — contents:write is "
        "required for the squash merge itself."
    )
    assert "permission-pull-requests: write" in text, (
        "down-scoping via permission-* drops inherited scopes — pull-requests:write "
        "is required by enablePullRequestAutoMerge."
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


def test_workflow_documents_workflows_permission_requirement():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # AC1 (#1234): the App-side grant is a manual UI action with no code trace of
    # its own. If the header and the setup instructions don't name it, a fresh
    # repo install reproduces the exact bug this issue fixed — auto-merge that
    # works everywhere except on CI PRs.
    assert "Workflows: Read and write" in text, (
        "the App permission list (header + Assert step setup instructions) must "
        "name Workflows: Read and write — it has no other trace in code (#1234)."
    )


def test_workflow_carve_out_compares_code_review_yml_content():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # AC4 (#1234): claude-code-action skips with "Skipping action due to workflow
    # validation" when its OWN calling workflow's content differs from the default
    # branch's, and the `Verify review verdict` ladder then falls through to
    # "plugin legitimately skipped — treating as pass". The required `review`
    # context goes green with ZERO review comments (verified live: PR #1231, run
    # 29992799097). Granting the workflows scope removes the accidental
    # last-line-of-defense that used to block those merges, so this carve-out
    # replaces it.
    assert ".github/workflows/code-review.yml" in text, (
        "carve-out dropped — a PR whose code-review.yml diverges from the default "
        "branch would arm auto-merge and merge on a comment-less green `review`."
    )
    # The predicate must be a two-dot CONTENT comparison against the default
    # branch, not a merge-base diff / changed-files list: the action's validation
    # is content-based, so it also fires on a branch merely STALE on
    # code-review.yml — a strictly wider class than "this PR edits the file".
    assert "github.event.repository.default_branch" in text, (
        "carve-out must compare against the default branch's content (two-dot), "
        "not the merge base — a stale branch trips the same validation."
    )


def test_workflow_carve_out_is_non_failure_and_visible():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # AC4: an expected-red check on a whole class of PRs trains us to stop reading
    # it — which is exactly how a REAL auto-merge failure gets missed. The carve-out
    # must therefore exit non-failure, and it must leave a signal on the PR (the
    # owner has to know a manual merge is owed).
    assert "CARVE_OUT_LABEL" in text, (
        "the carve-out must apply a label — it is the visible signal on the PR AND "
        "the field merge-train's `gh pr list --json labels` selector reads (AC5)."
    )
    assert "automerge-withheld:review-blind" in text, (
        "carve-out label name changed — merge-train.yml's selector matches this "
        "literal; renaming one side silently strands carve-out PRs behind main."
    )


def test_workflow_surfaces_permission_specific_remediation():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # AC6 (#1234): the generic "Failed to enable auto-merge on still-open PR"
    # error cost a full debugging cycle to trace back to a missing App permission.
    # When the underlying gh failure IS that refusal, say so.
    # The workflow greps for the refusal substring; in YAML-embedded bash the
    # backticks are backslash-escaped, so match that literal form.
    assert r"without \`workflows\` permission" in text, (
        "the error path must detect GitHub's workflows-permission refusal string "
        "and surface a permission-specific remediation line (#1234 AC6)."
    )
    assert "Workflows: Read and write" in text


def test_workflow_documents_carve_out_removal_condition():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # AC11: the carve-out is a STOPGAP. The real fix belongs in the required
    # `Verify review verdict` gate (#1236). Without an in-code removal condition
    # this becomes permanent scaffolding nobody dares delete.
    assert "#1236" in text, (
        "the carve-out must document that it is removable once #1236 lands — "
        "otherwise it outlives the hole it patches."
    )


def test_auto_merge_enable_is_sole_requester_of_workflows_scope():
    # AC8 (#1234): belt behind AC2's explicit down-scoping. The Workflows grant
    # lives on the *installation*, so every other workflow minting a token from
    # the same 'jarvis-ci' App could quietly acquire the widened scope. Pin the
    # blast radius: auto-merge-enable.yml is the only workflow that may ask for
    # it. A new consumer must land here deliberately, with a reviewer looking at
    # why a second workflow needs write access to CI definitions.
    workflows_dir = WORKFLOW_PATH.parent
    requesters = sorted(
        p.name
        for p in workflows_dir.glob("*.yml")
        if "permission-workflows" in p.read_text(encoding="utf-8")
    )
    assert requesters == [WORKFLOW_PATH.name], (
        "only auto-merge-enable.yml may request the workflows App-token scope; "
        f"found: {requesters}. If another workflow genuinely needs it, justify "
        "the widened blast radius before relaxing this invariant (#1234 AC8)."
    )


def test_auto_merge_enable_is_not_a_canon_baseline_file():
    # AC10 (#1234): scripts/repo_baseline/canon/ files are mirrored into other
    # repos, so a change there owes a parity slice. auto-merge-enable.yml is
    # jarvis-local — this test pins that, so if it is ever promoted to canon the
    # red forces the parity question instead of silently skipping it.
    canon_dir = WORKFLOW_PATH.resolve().parents[2] / "scripts" / "repo_baseline" / "canon"
    assert not (canon_dir / WORKFLOW_PATH.name).exists(), (
        "auto-merge-enable.yml became a canon baseline file — the #1234 carve-out "
        "and workflows-scope grant now owe a canon-parity slice."
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
