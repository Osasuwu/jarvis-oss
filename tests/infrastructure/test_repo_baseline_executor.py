"""Tests for scripts/repo_baseline/executor.py — live sync-PR executor.

Mocks at the GhRunner/GhWriteRunner seam via FakeRunner/FakeWriteRunner
(conftest.py) — no subprocess, no network, per AC10.
"""

from __future__ import annotations

import base64

import pytest
from conftest import FakeRunner, FakeWriteRunner, _jarvis_responses, _workflow_b64

from scripts.repo_baseline.applier import GhCall, GhCallKind
from scripts.repo_baseline.auditor import (
    BASELINE_REPOS,
    BranchProtection,
    RepoSettings,
    RepoSnapshot,
)
from scripts.repo_baseline.executor import (
    ACCOUNT_LOGIN,
    ACCOUNT_PASSES,
    SYNC_BRANCH,
    IdentityError,
    RepoOutcome,
    apply_protection,
    diff_phase,
    ensure_sync_branch,
    evaluate_protection_guard,
    execute_account_pass,
    execute_repo,
    find_sync_pr,
    format_outcome,
    preflight_identity,
    render_pr_body,
    resolve_repos,
    split_calls,
    sync_pr_status,
)
from scripts.repo_baseline.manifest import Manifest

PULLS_PATH = "repos/Osasuwu/jarvis/pulls?head=Osasuwu:repo-baseline/sync&state=all"


def _manifest(**kw) -> Manifest:
    base = {
        "repo": "Osasuwu/jarvis",
        "managed_files": ["a.yml"],
        "language_test_files": [],
        "required_check_contexts": [],
    }
    base.update(kw)
    return Manifest.from_dict(base)


def _pr(
    ref: str = SYNC_BRANCH, state: str = "open", merged_at=None, html_url: str = "https://x/pr/1"
):
    return {"head": {"ref": ref}, "state": state, "merged_at": merged_at, "html_url": html_url}


def _snapshot(*, protection=None) -> RepoSnapshot:
    return RepoSnapshot(
        repo="Osasuwu/jarvis",
        settings=RepoSettings(default_branch="main"),
        labels=[],
        workflows=[],
        branch_protection=protection,
        dependabot_ecosystems=[],
    )


# The first-listed owner in config/repos.conf forms the primary account
# pass; on a fresh clone this resolves to the example-owner fallback.
PRIMARY_ACCOUNT = next(iter(ACCOUNT_PASSES))


# ── resolve_repos ────────────────────────────────────────────────────────


def test_resolve_repos_unknown_account_raises():
    with pytest.raises(ValueError):
        resolve_repos("nope")


def test_resolve_repos_all_repos_when_repo_none():
    assert resolve_repos(PRIMARY_ACCOUNT) == BASELINE_REPOS


def test_resolve_repos_single_repo_must_belong_to_account():
    assert resolve_repos(PRIMARY_ACCOUNT, BASELINE_REPOS[0]) == [BASELINE_REPOS[0]]


def test_resolve_repos_rejects_repo_not_in_account():
    with pytest.raises(ValueError):
        resolve_repos(PRIMARY_ACCOUNT, "someone-else/not-in-scope")


# ── preflight_identity ──────────────────────────────────────────────────


def test_preflight_identity_accepts_matching_login():
    runner = FakeRunner({"user": {"login": ACCOUNT_LOGIN[PRIMARY_ACCOUNT]}})
    preflight_identity(runner, account=PRIMARY_ACCOUNT)


def test_preflight_identity_rejects_mismatched_login():
    runner = FakeRunner({"user": {"login": "SomeoneElse"}})
    with pytest.raises(IdentityError):
        preflight_identity(runner, account=PRIMARY_ACCOUNT)


# ── find_sync_pr / sync_pr_status ───────────────────────────────────────


def test_find_sync_pr_returns_none_when_no_matching_branch():
    runner = FakeRunner({PULLS_PATH: [{"head": {"ref": "other-branch"}, "state": "open"}]})
    assert find_sync_pr(runner, "Osasuwu/jarvis") is None


def test_find_sync_pr_returns_matching_pr():
    pr = _pr()
    runner = FakeRunner({PULLS_PATH: [pr]})
    assert find_sync_pr(runner, "Osasuwu/jarvis") == pr


def test_sync_pr_status_variants():
    assert sync_pr_status(None) == "none"
    assert sync_pr_status(_pr(state="open")) == "pending"
    assert sync_pr_status(_pr(state="closed", merged_at=None)) == "declined"
    assert sync_pr_status(_pr(state="closed", merged_at="2026-01-01T00:00:00Z")) == "none"


# ── split_calls ──────────────────────────────────────────────────────────


def test_split_calls_separates_file_and_context_calls():
    put_call = GhCall(kind=GhCallKind.PUT_FILE, path="a.yml", content="x", file_class="managed")
    del_call = GhCall(kind=GhCallKind.DELETE_FILE, path="b.yml")
    ctx_call = GhCall(
        kind=GhCallKind.SET_CHECK_CONTEXTS, path="<repo-settings>", contexts=("review",)
    )
    file_calls, context_call = split_calls([put_call, del_call, ctx_call])
    assert file_calls == [put_call, del_call]
    assert context_call == ctx_call


def test_split_calls_context_call_none_when_absent():
    put_call = GhCall(kind=GhCallKind.PUT_FILE, path="a.yml", content="x")
    file_calls, context_call = split_calls([put_call])
    assert file_calls == [put_call]
    assert context_call is None


# ── diff_phase ───────────────────────────────────────────────────────────


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_diff_phase_skips_identical_content():
    call = GhCall(kind=GhCallKind.PUT_FILE, path="a.yml", content="same\n", file_class="managed")
    runner = FakeRunner(
        {
            "repos/Osasuwu/jarvis/contents/a.yml?ref=main": {
                "sha": "deadbeef",
                "content": _b64("same\n"),
                "encoding": "base64",
            }
        }
    )
    write_ops, skipped = diff_phase(runner, "Osasuwu/jarvis", "main", [call])
    assert write_ops == []
    assert skipped == [call]


def test_diff_phase_emits_write_op_on_content_mismatch():
    call = GhCall(kind=GhCallKind.PUT_FILE, path="a.yml", content="new\n", file_class="managed")
    runner = FakeRunner(
        {
            "repos/Osasuwu/jarvis/contents/a.yml?ref=main": {
                "sha": "deadbeef",
                "content": _b64("old\n"),
                "encoding": "base64",
            }
        }
    )
    write_ops, skipped = diff_phase(runner, "Osasuwu/jarvis", "main", [call])
    assert len(write_ops) == 1
    assert write_ops[0].call == call
    assert write_ops[0].blob_sha == "deadbeef"
    assert skipped == []


def test_diff_phase_new_file_has_no_blob_sha():
    call = GhCall(kind=GhCallKind.PUT_FILE, path="a.yml", content="new\n", file_class="managed")
    runner = FakeRunner({}, not_found={"repos/Osasuwu/jarvis/contents/a.yml?ref=main"})
    write_ops, skipped = diff_phase(runner, "Osasuwu/jarvis", "main", [call])
    assert len(write_ops) == 1
    assert write_ops[0].blob_sha is None
    assert skipped == []


def test_diff_phase_delete_carries_sha_when_present():
    call = GhCall(kind=GhCallKind.DELETE_FILE, path=".github/workflows/stale.yml")
    runner = FakeRunner(
        {"repos/Osasuwu/jarvis/contents/.github/workflows/stale.yml?ref=main": {"sha": "cafebabe"}}
    )
    write_ops, skipped = diff_phase(runner, "Osasuwu/jarvis", "main", [call])
    assert len(write_ops) == 1
    assert write_ops[0].blob_sha == "cafebabe"
    assert skipped == []


def test_diff_phase_delete_skipped_when_already_absent():
    call = GhCall(kind=GhCallKind.DELETE_FILE, path=".github/workflows/gone.yml")
    runner = FakeRunner(
        {}, not_found={"repos/Osasuwu/jarvis/contents/.github/workflows/gone.yml?ref=main"}
    )
    write_ops, skipped = diff_phase(runner, "Osasuwu/jarvis", "main", [call])
    assert write_ops == []
    assert skipped == [call]


# ── evaluate_protection_guard ───────────────────────────────────────────


def test_guard_reportable_when_context_already_live():
    snapshot = _snapshot(protection=BranchProtection(strict=True, contexts=["review"]))
    reportable, reason = evaluate_protection_guard(
        ["review"], snapshot, file_exists=lambda _p: False
    )
    assert reportable is True
    assert reason is None


def test_guard_reportable_when_mapped_and_file_present():
    snapshot = _snapshot(protection=None)
    reportable, reason = evaluate_protection_guard(
        ["pytest"], snapshot, file_exists=lambda _p: True
    )
    assert reportable is True
    assert reason is None


def test_guard_not_reportable_when_mapped_but_file_absent():
    snapshot = _snapshot(protection=None)
    reportable, reason = evaluate_protection_guard(
        ["pytest"], snapshot, file_exists=lambda _p: False
    )
    assert reportable is False
    assert reason


def test_guard_not_reportable_when_unmapped_and_never_checks_file():
    snapshot = _snapshot(protection=None)
    calls = []

    def _file_exists(path):
        calls.append(path)
        return True

    reportable, reason = evaluate_protection_guard(
        ["unmapped-ctx"], snapshot, file_exists=_file_exists
    )
    assert reportable is False
    assert reason
    assert calls == []


# ── apply_protection ─────────────────────────────────────────────────────


def test_apply_protection_defers_on_unreportable_context():
    snapshot = _snapshot(protection=None)
    runner = FakeRunner({})
    write_runner = FakeWriteRunner()
    status = apply_protection(
        runner, write_runner, "Osasuwu/jarvis", "main", ["unmapped-ctx"], snapshot
    )
    assert status.startswith("deferred:")
    assert write_runner.calls == []


def test_apply_protection_patches_when_protection_exists():
    snapshot = _snapshot(protection=BranchProtection(strict=True, contexts=["review"]))
    runner = FakeRunner({})
    write_runner = FakeWriteRunner()
    status = apply_protection(runner, write_runner, "Osasuwu/jarvis", "main", ["review"], snapshot)
    assert status == "applied"
    assert len(write_runner.calls) == 1
    method, path, body = write_runner.calls[0]
    assert method == "PATCH"
    assert path == "repos/Osasuwu/jarvis/branches/main/protection/required_status_checks"
    assert body == {"strict": True, "contexts": ["review"]}


def test_apply_protection_raises_identity_error_when_account_mismatched():
    # account is opt-in (None skips AC1 entirely, see the tests above); when
    # a caller does pass it, a mismatch must raise before any write.
    snapshot = _snapshot(protection=BranchProtection(strict=True, contexts=["review"]))
    runner = FakeRunner({"user": {"login": "SomeoneElse"}})
    write_runner = FakeWriteRunner()
    with pytest.raises(IdentityError):
        apply_protection(
            runner,
            write_runner,
            "Osasuwu/jarvis",
            "main",
            ["review"],
            snapshot,
            account=PRIMARY_ACCOUNT,
        )
    assert write_runner.calls == []


def test_apply_protection_puts_full_payload_when_no_protection():
    snapshot = _snapshot(protection=None)
    runner = FakeRunner(
        {"repos/Osasuwu/jarvis/contents/.github/workflows/pytest.yml?ref=main": {"sha": "x"}}
    )
    write_runner = FakeWriteRunner()
    status = apply_protection(runner, write_runner, "Osasuwu/jarvis", "main", ["pytest"], snapshot)
    assert status == "applied"
    assert len(write_runner.calls) == 1
    method, path, body = write_runner.calls[0]
    assert method == "PUT"
    assert path == "repos/Osasuwu/jarvis/branches/main/protection"
    assert body["required_status_checks"] == {"strict": False, "contexts": ["pytest"]}
    assert body["enforce_admins"] is False
    assert body["required_pull_request_reviews"] is None
    assert body["restrictions"] is None


# ── render_pr_body ───────────────────────────────────────────────────────


def test_render_pr_body_lists_file_calls_sorted_by_path():
    calls = [
        GhCall(kind=GhCallKind.PUT_FILE, path="z.yml", content="x", file_class="managed"),
        GhCall(kind=GhCallKind.DELETE_FILE, path="a.yml"),
    ]
    body = render_pr_body(_manifest(), calls)
    assert body.index("a.yml") < body.index("z.yml")
    assert "delete" in body
    assert "update" in body


def test_render_pr_body_includes_required_contexts_section():
    calls = [
        GhCall(
            kind=GhCallKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            contexts=("review", "pytest"),
        )
    ]
    body = render_pr_body(_manifest(), calls)
    assert "Required check contexts" in body
    assert "review" in body
    assert "pytest" in body


def test_render_pr_body_warns_on_self_modifying_gate():
    calls = [
        GhCall(
            kind=GhCallKind.PUT_FILE,
            path=".github/workflows/code-review.yml",
            content="x",
            file_class="managed",
        )
    ]
    body = render_pr_body(_manifest(), calls)
    assert "Self-modifying gate" in body


def test_render_pr_body_no_gate_warning_when_not_touched():
    calls = [GhCall(kind=GhCallKind.PUT_FILE, path="a.yml", content="x", file_class="managed")]
    body = render_pr_body(_manifest(), calls)
    assert "Self-modifying gate" not in body


# ── ensure_sync_branch ───────────────────────────────────────────────────


def test_ensure_sync_branch_resets_when_branch_exists():
    write_runner = FakeWriteRunner()
    runner = FakeRunner(
        {
            "repos/Osasuwu/jarvis/git/refs/heads/main": {"object": {"sha": "base-sha"}},
            f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}": {"object": {"sha": "old-sha"}},
        }
    )
    ensure_sync_branch(runner, write_runner, "Osasuwu/jarvis", "main")
    assert len(write_runner.calls) == 1
    method, path, body = write_runner.calls[0]
    assert method == "PATCH"
    assert path == f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}"
    assert body == {"sha": "base-sha", "force": True}


def test_ensure_sync_branch_creates_when_branch_absent():
    write_runner = FakeWriteRunner()
    runner = FakeRunner(
        {"repos/Osasuwu/jarvis/git/refs/heads/main": {"object": {"sha": "base-sha"}}},
        not_found={f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}"},
    )
    ensure_sync_branch(runner, write_runner, "Osasuwu/jarvis", "main")
    assert len(write_runner.calls) == 1
    method, path, body = write_runner.calls[0]
    assert method == "POST"
    assert path == "repos/Osasuwu/jarvis/git/refs"
    assert body == {"ref": f"refs/heads/{SYNC_BRANCH}", "sha": "base-sha"}


# ── execute_repo — AC10 minimum cases + AC7 ─────────────────────────────


def test_execute_repo_zero_write_issues_zero_mutating_calls():
    manifest = _manifest(managed_files=[], language_test_files=[], required_check_contexts=[])
    responses = _jarvis_responses()
    responses[PULLS_PATH] = []
    runner = FakeRunner(responses)
    write_runner = FakeWriteRunner()

    outcome = execute_repo(
        "Osasuwu/jarvis", manifest, runner=runner, write_runner=write_runner, execute=True
    )

    assert outcome.file_status == "skipped"
    assert outcome.protection_status == "skipped"
    assert write_runner.calls == []


def test_execute_repo_delete_carries_blob_sha():
    manifest = _manifest(
        managed_files=[], language_test_files=[], required_check_contexts=[], prune=True
    )
    responses = _jarvis_responses()
    responses["repos/Osasuwu/jarvis/actions/workflows?per_page=100"] = {
        "workflows": [{"path": ".github/workflows/stale.yml", "name": "stale"}]
    }
    responses[PULLS_PATH] = []
    responses["repos/Osasuwu/jarvis/contents/.github/workflows/stale.yml?ref=main"] = {
        "sha": "stale-sha"
    }
    # The auditor also reads each workflow's body to observe its runs-on (#1406).
    responses["repos/Osasuwu/jarvis/contents/.github/workflows/stale.yml"] = _workflow_b64(
        "name: stale\njobs:\n  stale:\n    runs-on: ubuntu-latest\n"
    )
    responses["repos/Osasuwu/jarvis/git/refs/heads/main"] = {"object": {"sha": "base-sha"}}
    not_found = {f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}"}
    runner = FakeRunner(responses, not_found=not_found)
    write_runner = FakeWriteRunner(
        responses={("POST", "repos/Osasuwu/jarvis/pulls"): {"html_url": "https://x/pr/9"}}
    )

    outcome = execute_repo(
        "Osasuwu/jarvis", manifest, runner=runner, write_runner=write_runner, execute=True
    )

    assert outcome.file_status == "applied"
    assert outcome.pr_url == "https://x/pr/9"
    delete_calls = [c for c in write_runner.calls if c[0] == "DELETE"]
    assert len(delete_calls) == 1
    _, path, body = delete_calls[0]
    assert path == "repos/Osasuwu/jarvis/contents/.github/workflows/stale.yml"
    assert body["sha"] == "stale-sha"
    branch_create_calls = [
        c for c in write_runner.calls if c[0] == "POST" and c[1] == "repos/Osasuwu/jarvis/git/refs"
    ]
    assert len(branch_create_calls) == 1
    assert branch_create_calls[0][2]["sha"] == "base-sha"


def test_execute_repo_renders_canon_with_the_observed_runner_class():
    """#1406 — the live path must render ``{{ runs_on }}`` from what the repo's
    own workflows actually run on, with no manifest edit anywhere. This is the
    seam that shipped GitHub-hosted workflows into a billing-blocked account:
    the executor built its Applier without the snapshot it had just audited, so
    the profile default silently won.
    """
    manifest = _manifest(managed_files=["a.yml"], language_test_files=[], prune=False)
    responses = _jarvis_responses()
    responses["repos/Osasuwu/jarvis/actions/workflows?per_page=100"] = {
        "workflows": [{"path": ".github/workflows/build.yml", "name": "build"}]
    }
    responses["repos/Osasuwu/jarvis/contents/.github/workflows/build.yml"] = _workflow_b64(
        "jobs:\n  build:\n    runs-on: [self-hosted, linux, x64]\n"
    )
    responses[PULLS_PATH] = []
    responses["repos/Osasuwu/jarvis/git/refs/heads/main"] = {"object": {"sha": "base-sha"}}
    runner = FakeRunner(
        responses,
        not_found={
            f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}",
            "repos/Osasuwu/jarvis/contents/a.yml?ref=main",
        },
    )
    write_runner = FakeWriteRunner(
        responses={("POST", "repos/Osasuwu/jarvis/pulls"): {"html_url": "https://x/pr/9"}}
    )

    execute_repo(
        "Osasuwu/jarvis",
        manifest,
        runner=runner,
        write_runner=write_runner,
        execute=True,
        canon={"a.yml": "runs-on: {{ runs_on }}\n"},
    )

    puts = [c for c in write_runner.calls if c[0] == "PUT" and c[1].endswith("/contents/a.yml")]
    assert len(puts) == 1
    content = base64.b64decode(puts[0][2]["content"]).decode("utf-8")
    assert content == "runs-on: [self-hosted, linux, x64]\n"


def test_execute_repo_discloses_the_ci_meta_skip_to_the_operator():
    """#1406 — a skipped file is silent by construction (nothing is written),
    so the outcome is the only place an operator can learn ci-meta.yml was
    dropped. ``plan_account_pass`` already carries it in ``RepoPlan.notes``;
    the CLI runs through ``execute_repo``, which must disclose it too, or the
    live path re-hides exactly what the skip was added to make visible.
    """
    manifest = _manifest(managed_files=[".github/workflows/ci-meta.yml"], prune=False)
    responses = _jarvis_responses()
    responses[PULLS_PATH] = []
    runner = FakeRunner(responses, not_found={"repos/Osasuwu/jarvis/contents/tests/ci"})
    write_runner = FakeWriteRunner()

    outcome = execute_repo(
        "Osasuwu/jarvis",
        manifest,
        runner=runner,
        write_runner=write_runner,
        execute=True,
        canon={"ci-meta.yml": "runs-on: {{ runs_on }}\n"},  # canon is keyed by basename
    )

    assert outcome.file_status == "skipped"
    assert any("tests/ci" in note for note in outcome.notes)
    assert write_runner.calls == []


def test_format_outcome_prints_notes_under_the_repo_line():
    """#1406 — carrying notes on the outcome is only half the disclosure; the
    CLI is where an operator actually reads them.
    """
    outcome = RepoOutcome(
        repo="Osasuwu/jarvis",
        file_status="skipped",
        protection_status="skipped",
        notes=["ci-meta.yml: skipped — the repo has no tests/ci directory"],
    )

    lines = format_outcome(outcome).splitlines()

    assert lines[0].startswith("Osasuwu/jarvis: files=skipped")
    assert lines[1].strip().startswith("note: ci-meta.yml: skipped")


def test_format_outcome_is_a_single_line_when_there_is_nothing_to_disclose():
    outcome = RepoOutcome(repo="Osasuwu/jarvis", file_status="applied", protection_status="applied")

    assert format_outcome(outcome).splitlines() == [
        "Osasuwu/jarvis: files=applied protection=applied"
    ]


def test_execute_repo_declined_pr_skips_repo():
    manifest = _manifest(
        managed_files=["a.yml"], language_test_files=[], required_check_contexts=[]
    )
    responses = _jarvis_responses()
    responses[PULLS_PATH] = [_pr(state="closed", merged_at=None)]
    runner = FakeRunner(responses)
    write_runner = FakeWriteRunner()

    outcome = execute_repo(
        "Osasuwu/jarvis",
        manifest,
        runner=runner,
        write_runner=write_runner,
        execute=True,
        canon={"a.yml": "content-a\n"},
    )

    assert outcome.file_status == "declined"
    assert outcome.protection_status == "skipped"
    assert outcome.pr_url == "https://x/pr/1"
    assert write_runner.calls == []


def test_execute_repo_guard_defers_on_unreportable_context():
    manifest = _manifest(
        managed_files=[], language_test_files=[], required_check_contexts=["unmapped-ctx"]
    )
    responses = _jarvis_responses()
    responses[PULLS_PATH] = []
    runner = FakeRunner(responses)
    write_runner = FakeWriteRunner()

    outcome = execute_repo(
        "Osasuwu/jarvis", manifest, runner=runner, write_runner=write_runner, execute=True
    )

    assert outcome.file_status == "skipped"
    assert outcome.protection_status.startswith("deferred:")
    assert write_runner.calls == []


def test_execute_repo_identity_mismatch_defers_only_protection_write():
    # #1401: identity is checked per-write, not once for the whole pass — a
    # repo whose plan includes a reportable protection write defers just
    # that write ("deferred: ...") while the file/PR path is untouched.
    manifest = _manifest(
        managed_files=[], language_test_files=[], required_check_contexts=["review"]
    )
    responses = _jarvis_responses()
    responses[PULLS_PATH] = []
    responses["user"] = {"login": "SomeoneElse"}
    runner = FakeRunner(responses)
    write_runner = FakeWriteRunner()

    outcome = execute_repo(
        "Osasuwu/jarvis",
        manifest,
        runner=runner,
        write_runner=write_runner,
        execute=True,
        account=PRIMARY_ACCOUNT,
    )

    assert outcome.file_status == "skipped"
    assert outcome.protection_status.startswith("deferred:")
    assert write_runner.calls == []


def test_execute_account_pass_identity_mismatch_does_not_block_file_writes():
    # A mismatched identity must not abort push-level writes for repos whose
    # plan has no protection action at all — only apply_protection gates on it.
    manifest = _manifest(
        managed_files=["a.yml"], language_test_files=[], required_check_contexts=[]
    )
    responses = _jarvis_responses()
    responses[PULLS_PATH] = []
    responses["repos/Osasuwu/jarvis/git/refs/heads/main"] = {"object": {"sha": "base-sha"}}
    not_found = {
        "repos/Osasuwu/jarvis/contents/a.yml?ref=main",
        f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}",
    }
    runner = FakeRunner(responses, not_found=not_found)
    write_runner = FakeWriteRunner(
        responses={("POST", "repos/Osasuwu/jarvis/pulls"): {"html_url": "https://x/pr/9"}}
    )

    outcome = execute_repo(
        "Osasuwu/jarvis",
        manifest,
        runner=runner,
        write_runner=write_runner,
        execute=True,
        account=PRIMARY_ACCOUNT,
        canon={"a.yml": "content\n"},
    )

    assert outcome.file_status == "applied"
    assert outcome.pr_url == "https://x/pr/9"
    assert outcome.protection_status == "skipped"
    # No "user" response was configured at all — proves preflight_identity
    # was never even called on this path.
    assert ("user", False) not in runner.calls


def test_execute_repo_fails_fast_on_write_error():
    manifest = _manifest(
        managed_files=["a.yml"], language_test_files=[], required_check_contexts=[]
    )
    responses = _jarvis_responses()
    responses[PULLS_PATH] = []
    responses["repos/Osasuwu/jarvis/git/refs/heads/main"] = {"object": {"sha": "base-sha"}}
    not_found = {
        "repos/Osasuwu/jarvis/contents/a.yml?ref=main",
        f"repos/Osasuwu/jarvis/git/refs/heads/{SYNC_BRANCH}",
    }
    runner = FakeRunner(responses, not_found=not_found)
    write_runner = FakeWriteRunner(
        raise_for={("PUT", "repos/Osasuwu/jarvis/contents/a.yml"): RuntimeError("boom")}
    )

    outcome = execute_repo(
        "Osasuwu/jarvis",
        manifest,
        runner=runner,
        write_runner=write_runner,
        execute=True,
        canon={"a.yml": "content\n"},
    )

    assert outcome.file_status.startswith("failed:")
    assert "boom" in outcome.file_status
    assert outcome.protection_status == "skipped"
    assert outcome.pr_url is None
    pulls_calls = [
        c for c in write_runner.calls if c[0] == "POST" and c[1] == "repos/Osasuwu/jarvis/pulls"
    ]
    assert pulls_calls == []


# ── execute_account_pass — AC2 per-repo isolation ───────────────────────


def test_execute_account_pass_isolates_per_repo_errors():
    responses = {}
    not_found = set()
    for repo in BASELINE_REPOS:
        responses[f"repos/{repo}"] = {"default_branch": "main"}
        responses[f"repos/{repo}/labels"] = []
        responses[f"repos/{repo}/actions/workflows?per_page=100"] = {"workflows": []}
        not_found.add(f"repos/{repo}/branches/main/protection")
        not_found.add(f"repos/{repo}/contents/.github/dependabot.yml")
        not_found.add(f"repos/{repo}/contents/tests/ci")

    failing_repo = BASELINE_REPOS[-1]
    raise_for = {f"repos/{failing_repo}": RuntimeError("gh api boom")}
    runner = FakeRunner(responses, not_found=not_found, raise_for=raise_for)
    write_runner = FakeWriteRunner()

    outcomes = execute_account_pass(
        PRIMARY_ACCOUNT, runner=runner, write_runner=write_runner, execute=False, canon={}
    )

    assert [o.repo for o in outcomes] == BASELINE_REPOS
    by_repo = {o.repo: o for o in outcomes}

    failed = by_repo[failing_repo]
    assert failed.file_status == "errored"
    assert failed.protection_status == "skipped"
    assert "gh api boom" in failed.error

    for repo in BASELINE_REPOS:
        if repo == failing_repo:
            continue
        assert by_repo[repo].file_status == "gapped"
        assert by_repo[repo].protection_status == "skipped"

    assert write_runner.calls == []
