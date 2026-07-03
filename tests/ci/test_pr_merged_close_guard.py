"""Meta-test for .github/workflows/pr-merged.yml.

Reimplements the workflow's close/skip decision rule in Python and asserts it
closes/skips the issues it promises, plus a config dimension keeping the YAML
and this test in lockstep.

Why this test exists (#948 Bug A, #1012): native linked-issue auto-close does
NOT fire for automated (bot/App) merges — GitHub recursion-prevention suppresses
it. The App-token merge (auto-merge-enable.yml) un-suppresses the
`pull_request: closed` event, so pr-merged.yml is the deterministic close path.
A silent drift here (back to comment-only, or dropping the reopen guard) would
re-open Bug A with no red signal. pr-merged.yml is NOT path-filtered, so #326's
*config* mandate doesn't strictly apply — but the close/skip rule is exactly the
silent-drift class #326 targets, so the logic+config sibling test is warranted
(same rationale as test_merge_train_guard.py).

Decision rule mirrored here (see the bash loop in the YAML):
  - issue already CLOSED                        -> skip (idempotent)
  - issue reopened AFTER the PR's merged_at     -> skip (respect human reopen)
  - otherwise                                   -> close (state_reason=completed)
The candidate set is `closingIssuesReferences` (authoritative linkage), not a
body regex, projected WITH each ref's own repo (`owner.login`/`name`).
GITHUB_TOKEN is scoped to $REPO, so a cross-repo ref CANNOT be closed here — it
is SKIPPED with a warning, never collapsed into $REPO (which would close the
wrong same-numbered issue). The linkage read fails LOUD on a gh error rather
than treating an empty result as "nothing to close" (a silent green that would
re-open Bug A). A single close failure mid-loop does not abort the run — every
issue is attempted, then the job fails if any close failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "pr-merged.yml"
)


def decide(state: str, latest_reopen: str | None, merged_at: str) -> str:
    """Mirror the workflow's per-issue close/skip rule.

    Returns one of: "close", "skip-already-closed", "skip-reopened".
    Timestamps are ISO-8601 Z strings; lexicographic comparison matches the
    bash `[[ "$latest_reopen" > "$MERGED_AT" ]]` test.
    """
    if state == "CLOSED":
        return "skip-already-closed"
    if latest_reopen and latest_reopen > merged_at:
        return "skip-reopened"
    return "close"


MERGED_AT = "2026-06-21T12:00:00Z"


# ---- logic dimension --------------------------------------------------------


def test_open_never_reopened_is_closed():
    assert decide("OPEN", None, MERGED_AT) == "close"
    assert decide("OPEN", "", MERGED_AT) == "close"


def test_already_closed_is_skipped():
    # Idempotent: a re-delivered event or a backstop must not re-close.
    assert decide("CLOSED", None, MERGED_AT) == "skip-already-closed"


def test_reopened_after_merge_is_skipped():
    # Human reopened the issue after the PR merged -> respect it, leave open.
    assert decide("OPEN", "2026-06-21T13:00:00Z", MERGED_AT) == "skip-reopened"


def test_reopened_before_merge_is_closed():
    # A reopen that predates this merge is stale w.r.t. this PR -> still close.
    assert decide("OPEN", "2026-06-20T09:00:00Z", MERGED_AT) == "close"


def test_reopen_exactly_at_merge_is_closed():
    # Strict `>`: a reopen timestamped exactly at merge is not "after".
    assert decide("OPEN", MERGED_AT, MERGED_AT) == "close"


def test_closed_state_wins_over_reopen_signal():
    # If the issue is currently CLOSED, skip regardless of any reopen history.
    assert decide("CLOSED", "2026-06-21T13:00:00Z", MERGED_AT) == "skip-already-closed"


# ---- config dimension (keep YAML and test in lockstep) ----------------------


def test_workflow_exists():
    assert WORKFLOW_PATH.is_file(), "pr-merged.yml missing"


def test_workflow_triggers_on_pr_closed_to_main():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pull_request:" in text and "closed" in text, "must trigger on pull_request closed."
    assert "branches: [main]" in text, "must scope to merges into main."
    assert "github.event.pull_request.merged == true" in text, (
        "must act only on MERGED (not merely closed) PRs."
    )


def test_workflow_actually_closes_not_just_comments():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # The whole point of #1012: it must CLOSE, not only post a comment. A
    # `gh issue close --reason completed` is the close; comment-only is the
    # regression that left #1005 open with a false "Closed via" note.
    assert "gh issue close" in text, "workflow must close linked issues, not just comment."
    assert "--reason completed" in text, "closes must use state_reason=completed."


def test_workflow_uses_authoritative_linkage_not_regex():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Use closingIssuesReferences (the set native auto-close targets), not a body
    # regex — avoids malformed-ref leakage.
    assert "closingIssuesReferences" in text, (
        "candidate issues must come from closingIssuesReferences (authoritative linkage)."
    )


def test_workflow_fails_loud_on_linkage_read_failure():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # A `mapfile -t ... < <(gh pr view ...)` swallows gh's exit code (process
    # substitution is invisible to set -e), so a transient gh failure yields zero
    # refs -> green "nothing to close" with the issue still open (Bug A, silently).
    # The read must capture-then-check and exit non-zero on gh failure.
    assert "mapfile -t issues < <(" not in text, (
        "linkage read must not use `mapfile < <(gh ...)` — it swallows gh's exit "
        "code, turning a gh failure into a silent green no-op (#948 Bug A)."
    )
    assert "if ! refs=$(" in text, (
        "linkage read must capture-then-check (`if ! refs=$(gh pr view ...)`) so "
        "gh's exit code is visible to set -e — not a process-substitution read "
        "that swallows it (#948 Bug A)."
    )
    assert "gh pr view failed" in text, (
        "linkage read must fail loud (explicit exit 1) when gh pr view errors, "
        "not assume zero linked issues."
    )


def test_workflow_is_cross_repo_aware():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # closingIssuesReferences can include cross-repo issues. GITHUB_TOKEN is
    # scoped to $REPO, so a foreign ref CANNOT be closed here — it must be SKIPPED
    # with a warning, never collapsed into $REPO (which would close the wrong
    # same-numbered issue), and never silently dropped.
    # The repo slug MUST be built from `owner.login` + `name`, NOT
    # `.nameWithOwner`. The nested `repository` object on a
    # closingIssuesReferences ref (gh pr view --json closingIssuesReferences)
    # only exposes {id, name, owner{login}} — it has NO `nameWithOwner` field,
    # so `\(.repository.nameWithOwner)` resolves to the literal "null". That made
    # every same-repo ref read as "null", trip the cross-repo guard, and get
    # skipped under a green job (#1013/#1015/#1037/#939; systemic from #1021).
    # Pin the correct construction AND forbid the bogus field so a revert reds
    # this test. (This grep-only meta-test can't catch the null at runtime — it
    # never runs jq against real gh output — but it can at least pin the field
    # path that does exist.)
    assert ".repository.nameWithOwner" not in text, (
        "linkage projection must NOT use `.repository.nameWithOwner` — that field "
        "does not exist on the nested repository object and resolves to null, "
        "misclassifying every same-repo ref as cross-repo (#1021 regression)."
    )
    assert ".repository.owner.login" in text and ".repository.name" in text, (
        "linkage projection must build the repo slug from "
        "`.repository.owner.login`/`.repository.name` so a foreign ref is "
        "detected (then skipped), not collapsed into $REPO."
    )
    assert '"$irepo" != "$REPO"' in text, (
        "the loop must guard `$irepo != $REPO` and skip+warn — GITHUB_TOKEN is "
        "repo-scoped and cannot close a cross-repo issue (it would 403)."
    )
    assert 'repos/$irepo/issues' in text, (
        "the reopen-guard timeline lookup must be keyed on the issue's own repo "
        "($irepo), not the PR's repo."
    )
    assert '--repo "$irepo"' in text, (
        "gh issue close must target the ref's own repo ($irepo), not a hardcoded $REPO."
    )
    # `--repo "$irepo"` appears on both the state read and the close; pin the
    # STATE read specifically. A regression that reverted only the state read to
    # $REPO (closing the wrong same-numbered issue's state-check) would slip past
    # the generic assertion above (#1021 review).
    assert 'gh issue view "$n" --repo "$irepo"' in text, (
        "the state read must use the ref's own repo ($irepo), not $REPO — else a "
        "cross-repo ref's state is read from the wrong repo."
    )


def test_workflow_continues_past_a_single_close_failure():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # One `gh issue close` failure must not abort the loop under set -e and
    # strand the remaining linked issues unattempted. The loop records the
    # failure (failed=1) and the job fails AFTER every issue is attempted.
    assert "failed=1" in text, (
        "a mid-loop close failure must set failed=1 and continue, not abort the "
        "loop and leave later issues open (#948 Bug A — partial close)."
    )
    assert 'if [ "$failed" -ne 0 ]' in text, (
        "the job must fail after the loop when any close failed, so a partial "
        "close still surfaces a red signal."
    )
    # The load-bearing mechanism is the `if gh issue close …; then … else
    # failed=1; fi` wrapper: without the `if` guard, `set -e` aborts the loop on
    # the FIRST close failure and `failed=1` is never reached (#1021 review).
    assert "if gh issue close" in text, (
        "gh issue close must be wrapped in `if … ; then … else failed=1; fi` so "
        "set -e cannot abort the loop on the first close failure."
    )


def test_workflow_has_reopen_guard():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # A deliberate human reopen after merge must not be re-closed.
    assert "reopened" in text and "MERGED_AT" in text, (
        "reopen-after-merge guard (compare reopened event time vs merged_at) drifted."
    )


def test_workflow_reopen_guard_uses_global_sort_not_page_local_last():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # The reopen guard is the subtlest change in this workflow. `gh api --paginate
    # -q '<jq>'` evaluates the jq filter ONCE PER PAGE and streams — so an
    # aggregating jq expression (`[.[] | …] | last`) emits one page-local result
    # PER PAGE, not the global chronological max. On a multi-page timeline that
    # picks the wrong "latest reopen" and the guard misfires (#1021). The fix emits
    # one timestamp per reopened event across all pages, then takes the global max
    # with `sort | tail -1` (ISO-8601 Z sorts lexicographically = chronologically).
    # A future revert to `| last` would pass every other test and silently
    # re-introduce the page-local bug — so pin it here.
    assert '[.[] | select(.event=="reopened") | .created_at] | last' not in text, (
        "reopen guard must NOT use jq `| last` over a paginated stream — --paginate "
        "runs jq per-page, so `last` yields the page-local max, not the global "
        "chronological max (#1021)."
    )
    assert "sort | tail -1" in text, (
        "reopen guard must collect reopened timestamps across all pages then "
        "`sort | tail -1` for the global chronological max (#1021)."
    )


def test_workflow_state_read_failure_fails_loud_not_silent():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # An unreadable issue state (transient rate-limit / network) must NOT be a
    # silent skip — that leaves the issue OPEN under a GREEN job, the same Bug A
    # failure mode as a close failure. The state-read tolerates the transient error
    # (`|| echo ""`) and the empty guard routes to failed=1 (red signal -> retry),
    # rather than a `continue` that strands the issue. Pin both halves so a revert
    # to a silent skip reds this test (#1021 review).
    assert (
        'state=$(gh issue view "$n" --repo "$irepo" --json state -q .state 2>/dev/null || echo "")'
        in text
    ), (
        "state read must tolerate a transient gh error (`|| echo \"\"`) then be "
        "checked — not abort the loop under set -e nor silently skip."
    )
    assert "Could not read" in text, (
        "the empty-state branch must emit a diagnostic and set failed=1 (fail the "
        "job for retry), not silently continue and leave the issue open (#1021)."
    )


def test_workflow_needs_issues_write():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "issues: write" in text, "closing issues requires issues: write permission."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
