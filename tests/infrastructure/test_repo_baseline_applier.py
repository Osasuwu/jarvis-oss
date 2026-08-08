"""Tests for the repo-baseline Applier (#939, milestone #48 slice 5).

The Applier is a pure translator (no network), so these tests need no live gh
boundary — they assert the exact ``GhCall`` sequence produced from a plan +
actual state, plus the orchestrator's dry-run report over the committed
fixtures.
"""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml

from scripts.repo_baseline.applier import (
    HASH_PREFIX,
    BASELINE_REPOS,
    ApplyError,
    Applier,
    GhCall,
    GhCallKind,
    RepoPlan,
    actual_state_from_snapshot,
    load_canon,
    load_manifest,
    load_snapshot,
    plan_account_pass,
    resolve_runs_on,
    summarize_account_pass,
)
from scripts.repo_baseline.auditor import BranchProtection, RepoSettings, RepoSnapshot
from scripts.repo_baseline.manifest import Manifest
from scripts.repo_baseline.planner import Action, ActionKind, ActualState, Planner


def _hash(text: str) -> str:
    # Must mirror the Applier's algorithm-tagged hash exactly, else the
    # idempotency comparison would never match (the tag is part of the contract).
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


# A canon map keyed by basename, covering exactly the paths a tiny test plan
# touches — so tests don't depend on the real (intentionally incomplete) canon.
_TEST_CANON = {
    "code-review.yml": "review for {{ code_review_marketplace }}\n",
    "owner-queue-guard.yml": "guard on {{ runs_on }}\n",
    "dependabot.yml": "target-branch: {{ default_branch }}\n",
    "ci-meta.yml": "run: pytest tests/ci/ -v{{ ci_meta_pytest_args }}\n",
}


def _manifest(**kw) -> Manifest:
    base = {"repo": "Osasuwu/test", "managed_files": [], "language_test_files": []}
    base.update(kw)
    return Manifest.from_dict(base)


# ── render_content ────────────────────────────────────────────────────


def test_render_content_substitutes_axes():
    applier = Applier(_manifest(), _TEST_CANON)
    out = applier.render_content(".github/workflows/code-review.yml")
    assert out == "review for anthropics/claude-code-action@v1\n"


def test_render_content_uses_the_observed_runner_class():
    """#1406 — the canon's ``{{ runs_on }}`` takes the observed majority, with
    no manifest edit and no operator prompt anywhere in the path."""
    snap = _snap({".github/workflows/build.yml": [["self-hosted", "linux", "x64"]]})
    applier = Applier(_manifest(), _TEST_CANON, snapshot=snap)
    out = applier.render_content(".github/workflows/owner-queue-guard.yml")
    assert out == "guard on [self-hosted, linux, x64]\n"
    assert applier.runs_on_defaulted is False


def test_render_content_runs_on_falls_back_to_profile_default():
    applier = Applier(_manifest(), _TEST_CANON, snapshot=_snap({}))
    assert applier.render_content(".github/workflows/owner-queue-guard.yml") == (
        "guard on [ubuntu-latest]\n"
    )
    assert applier.runs_on_defaulted is True


def test_render_content_uses_the_live_default_branch():
    """#1406 — ``default_branch`` is an observed axis too. Canon dependabot.yml
    hardcoded ``target-branch: main``, so every dependabot PR it opened against
    redrobot (default branch ``master``) targeted a branch that does not exist.
    """
    snap = _snap({}, default_branch="master")
    applier = Applier(_manifest(), _TEST_CANON, snapshot=snap)
    assert applier.render_content(".github/dependabot.yml") == "target-branch: master\n"


def test_render_content_missing_canon_raises_with_path():
    applier = Applier(_manifest(), _TEST_CANON)
    with pytest.raises(ApplyError) as exc:
        applier.render_content(".github/workflows/pytest.yml")
    # Error names both the repo-path and the expected canon basename.
    assert "pytest.yml" in str(exc.value)
    assert "Osasuwu/test" in str(exc.value)


# ── translate: WRITE_FILE ─────────────────────────────────────────────


def test_translate_write_file_emits_put_with_rendered_content():
    plan = [
        Action(
            kind=ActionKind.WRITE_FILE,
            path=".github/workflows/code-review.yml",
            file_class="managed",
        )
    ]
    applier = Applier(_manifest(), _TEST_CANON)
    calls = applier.translate(plan, ActualState())
    assert calls == [
        GhCall(
            kind=GhCallKind.PUT_FILE,
            path=".github/workflows/code-review.yml",
            content="review for anthropics/claude-code-action@v1\n",
            file_class="managed",
        )
    ]


def test_translate_write_file_idempotent_when_hash_matches():
    path = ".github/workflows/code-review.yml"
    rendered = "review for anthropics/claude-code-action@v1\n"
    plan = [Action(kind=ActionKind.WRITE_FILE, path=path, file_class="managed")]
    actual = ActualState(files={path: _hash(rendered)})
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert calls == []  # byte-identical on the repo → no write


def test_translate_write_file_emitted_when_hash_unknown():
    # The auditor records paths with an empty-string hash (body unknown) — the
    # write must still be emitted; the git layer dedupes downstream.
    path = ".github/workflows/code-review.yml"
    plan = [Action(kind=ActionKind.WRITE_FILE, path=path, file_class="managed")]
    actual = ActualState(files={path: ""})
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert [c.kind for c in calls] == [GhCallKind.PUT_FILE]


def test_translate_write_file_emitted_when_hash_differs():
    # A known-but-wrong hash (real tagged hash, different content) must emit the
    # PUT — guards against a mutation that drops the equality check.
    path = ".github/workflows/code-review.yml"
    plan = [Action(kind=ActionKind.WRITE_FILE, path=path, file_class="managed")]
    actual = ActualState(files={path: _hash("stale body that does not match\n")})
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert [c.kind for c in calls] == [GhCallKind.PUT_FILE]


def test_translate_raises_on_untagged_hash():
    # An actual hash missing the sha256: tag (e.g. a raw git-blob SHA1 from a
    # future auditor) would compare unequal forever — the contract guard must
    # reject it loudly rather than emit a spurious write every run.
    path = ".github/workflows/code-review.yml"
    plan = [Action(kind=ActionKind.WRITE_FILE, path=path, file_class="managed")]
    actual = ActualState(files={path: "deadbeef" * 5})  # 40-hex, untagged SHA1 shape
    with pytest.raises(ApplyError) as exc:
        Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert "sha256:" in str(exc.value)


# ── translate: DELETE_FILE ────────────────────────────────────────────


def test_translate_delete_only_when_present():
    plan = [
        Action(kind=ActionKind.DELETE_FILE, path=".github/workflows/old.yml"),
        Action(kind=ActionKind.DELETE_FILE, path=".github/workflows/gone.yml"),
    ]
    actual = ActualState(files={".github/workflows/old.yml": ""})
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert calls == [GhCall(kind=GhCallKind.DELETE_FILE, path=".github/workflows/old.yml")]


def test_translate_delete_noop_when_actual_files_empty():
    # A repo whose snapshot recorded no files (bare repo) must yield no deletes —
    # the existence guard makes every DELETE a no-op rather than a spurious call.
    plan = [Action(kind=ActionKind.DELETE_FILE, path=".github/workflows/old.yml")]
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, ActualState())
    assert calls == []


# ── translate: SET_CHECK_CONTEXTS ─────────────────────────────────────


def test_translate_set_contexts_emitted_when_differ():
    plan = [
        Action(
            kind=ActionKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            context_names=["review", "owner-queue-guard"],
        )
    ]
    actual = ActualState(required_check_contexts=["review"])
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert calls == [
        GhCall(
            kind=GhCallKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            contexts=("review", "owner-queue-guard"),
        )
    ]


def test_translate_set_contexts_idempotent_order_insensitive():
    plan = [
        Action(
            kind=ActionKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            context_names=["review", "owner-queue-guard"],
        )
    ]
    actual = ActualState(required_check_contexts=["owner-queue-guard", "review"])
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert calls == []  # same set, different order → no-op


def test_translate_set_contexts_both_empty_is_noop():
    # Desired empty + actual empty → already reconciled → no call.
    plan = [Action(kind=ActionKind.SET_CHECK_CONTEXTS, path="<repo-settings>", context_names=[])]
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, ActualState())
    assert calls == []


def test_translate_set_contexts_emitted_to_clean_up_actual_duplicate():
    # A live repo whose contexts came back with a duplicate (GitHub API quirk)
    # must be detected as drift and reconciled to the de-duplicated desired set —
    # a set() comparison would wrongly collapse the dup and skip the fix.
    plan = [
        Action(
            kind=ActionKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            context_names=["review", "owner-queue-guard"],
        )
    ]
    actual = ActualState(required_check_contexts=["review", "review", "owner-queue-guard"])
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, actual)
    assert calls == [
        GhCall(
            kind=GhCallKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            contexts=("review", "owner-queue-guard"),
        )
    ]


# ── ordering ──────────────────────────────────────────────────────────


def test_translate_preserves_plan_order():
    plan = [
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/code-review.yml"),
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/owner-queue-guard.yml"),
        Action(
            kind=ActionKind.SET_CHECK_CONTEXTS,
            path="<repo-settings>",
            context_names=["review"],
        ),
    ]
    calls = Applier(_manifest(), _TEST_CANON).translate(plan, ActualState())
    # Files-before-protection invariant carried through unchanged.
    assert [c.kind for c in calls] == [
        GhCallKind.PUT_FILE,
        GhCallKind.PUT_FILE,
        GhCallKind.SET_CHECK_CONTEXTS,
    ]


# ── missing_canon ─────────────────────────────────────────────────────


def test_missing_canon_flags_uncovered_writes():
    plan = [
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/code-review.yml"),
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/pytest.yml"),
        Action(kind=ActionKind.DELETE_FILE, path=".github/workflows/old.yml"),
    ]
    gaps = Applier(_manifest(), _TEST_CANON).missing_canon(plan)
    assert gaps == [".github/workflows/pytest.yml"]  # delete is not a canon need


def test_translate_raises_on_missing_canon_upfront():
    # translate() self-enforces the pre-flight: a WRITE for an uncovered path
    # raises BEFORE any call is emitted (no partial list discarded). A direct
    # caller that skips missing_canon() still gets a loud ApplyError naming it.
    plan = [
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/code-review.yml"),
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/pytest.yml"),
    ]
    with pytest.raises(ApplyError) as exc:
        Applier(_manifest(), _TEST_CANON).translate(plan, ActualState())
    assert "pytest.yml" in str(exc.value)


def test_translate_raises_on_basename_collision():
    # Two managed paths sharing a canon basename would both render the same
    # template — the invariant is enforced, not silently mis-rendered.
    canon = {"code-review.yml": "x\n"}
    plan = [
        Action(kind=ActionKind.WRITE_FILE, path=".github/workflows/code-review.yml"),
        Action(kind=ActionKind.WRITE_FILE, path=".github/other/code-review.yml"),
    ]
    with pytest.raises(ApplyError, match="basename collision"):
        Applier(_manifest(), canon).translate(plan, ActualState())


# ── snapshot → actual-state bridge ────────────────────────────────────


def test_actual_state_from_snapshot_maps_workflows_and_contexts():
    snap = RepoSnapshot(
        repo="Osasuwu/test",
        settings=RepoSettings(),
        workflows=[".github/workflows/code-review.yml", ".github/workflows/ci.yml"],
        branch_protection=BranchProtection(strict=True, contexts=["review", "pytest"]),
    )
    actual = actual_state_from_snapshot(snap)
    assert set(actual.files) == {
        ".github/workflows/code-review.yml",
        ".github/workflows/ci.yml",
    }
    assert all(v == "" for v in actual.files.values())  # bodies unknown
    assert actual.required_check_contexts == ["review", "pytest"]


def test_actual_state_from_snapshot_handles_no_branch_protection():
    snap = RepoSnapshot(
        repo="Osasuwu/bare", settings=RepoSettings(), workflows=[], branch_protection=None
    )
    actual = actual_state_from_snapshot(snap)
    assert actual.files == {}
    assert actual.required_check_contexts == []


# ── observed axes: runs_on resolution (#1406) ─────────────────────────


def _snap(
    observed: dict[str, list[list[str]]],
    repo: str = "Osasuwu/test",
    default_branch: str = "main",
    has_tests_ci: bool | None = None,
) -> RepoSnapshot:
    return RepoSnapshot(
        repo=repo,
        settings=RepoSettings(default_branch=default_branch),
        workflows=sorted(observed),
        branch_protection=None,
        observed_runners=observed,
        has_tests_ci=has_tests_ci,
    )


class TestResolveRunsOn:
    """``runs_on`` is a fact read from the repo's own workflows, not a policy
    declared in a manifest (#1406). Resolution is a *majority* over observed
    runner classes rather than a match-or-abort check, because a repo may
    legitimately mix: redrobot runs 13 of 14 workflows on ``[self-hosted, linux,
    x64]`` and deliberately keeps ``runner-health-check.yml`` on
    ``ubuntu-latest`` so the health check still reports when the self-hosted
    runner is down.
    """

    def test_majority_wins_over_a_deliberate_minority(self):
        observed = {
            ".github/workflows/a.yml": [["self-hosted", "linux", "x64"]],
            ".github/workflows/b.yml": [["self-hosted", "linux", "x64"]],
            ".github/workflows/health.yml": [["ubuntu-latest"]],
        }
        labels, defaulted = resolve_runs_on(_snap(observed), _manifest())
        assert labels == ["self-hosted", "linux", "x64"]
        assert defaulted is False

    def test_empty_observation_falls_back_to_profile_default_and_says_so(self):
        labels, defaulted = resolve_runs_on(_snap({}), _manifest())
        assert labels == ["ubuntu-latest"]
        assert defaulted is True

    def test_tie_is_broken_deterministically(self):
        """A 1-1 tie must not depend on dict iteration order — otherwise two
        runs over the same repo could render different workflows."""
        observed = {
            ".github/workflows/a.yml": [["self-hosted"]],
            ".github/workflows/b.yml": [["ubuntu-latest"]],
        }
        first, _ = resolve_runs_on(_snap(observed), _manifest())
        flipped = dict(reversed(list(observed.items())))
        second, _ = resolve_runs_on(_snap(flipped), _manifest())
        assert first == second

    def test_managed_files_are_not_read_back_as_evidence(self):
        """A prior mis-sync wrote its own wrong runner class into the managed
        set. Counting those votes would let the mistake justify itself — the
        exact loop that shipped ``ubuntu-latest`` into a billing-blocked
        account. Only the repo's own (non-managed) workflows vote.
        """
        managed = [
            ".github/workflows/owner-queue-guard.yml",
            ".github/workflows/pr-body-check.yml",
            ".github/workflows/ci-meta.yml",
        ]
        observed = {path: [["ubuntu-latest"]] for path in managed}
        observed[".github/workflows/build.yml"] = [["self-hosted", "linux", "x64"]]

        labels, defaulted = resolve_runs_on(_snap(observed), _manifest(managed_files=managed))

        assert labels == ["self-hosted", "linux", "x64"]
        assert defaulted is False

    def _seed(self, tmp_path, observed):
        """Write a minimal manifest + snapshot pair the loaders can read."""
        manifests, snapshots = tmp_path / "m", tmp_path / "s"
        manifests.mkdir()
        snapshots.mkdir()
        (manifests / "Osasuwu__test.manifest.yml").write_text(
            yaml.safe_dump(
                {"repo": "Osasuwu/test", "managed_files": [], "language_test_files": []}
            ),
            encoding="utf-8",
        )
        (snapshots / "Osasuwu__test.snapshot.json").write_text(
            json.dumps(_snap(observed).to_dict()), encoding="utf-8"
        )
        return manifests, snapshots

    def test_plan_output_says_when_the_runner_class_was_defaulted(self, tmp_path):
        manifests, snapshots = self._seed(tmp_path, {})
        (plan,) = plan_account_pass(
            ["Osasuwu/test"], canon={}, manifests_dir=manifests, snapshots_dir=snapshots
        )
        assert any("runs_on" in note and "default" in note for note in plan.notes), plan.notes

    def test_plan_output_is_quiet_when_the_runner_class_was_observed(self, tmp_path):
        manifests, snapshots = self._seed(
            tmp_path, {".github/workflows/build.yml": [["self-hosted", "linux", "x64"]]}
        )
        (plan,) = plan_account_pass(
            ["Osasuwu/test"], canon={}, manifests_dir=manifests, snapshots_dir=snapshots
        )
        assert not [note for note in plan.notes if "runs_on" in note]

    def test_all_observed_files_managed_falls_back_to_default(self):
        managed = [".github/workflows/ci-meta.yml"]
        labels, defaulted = resolve_runs_on(
            _snap({managed[0]: [["ubuntu-latest"]]}), _manifest(managed_files=managed)
        )
        assert labels == ["ubuntu-latest"]
        assert defaulted is True


# ── loaders over committed fixtures ───────────────────────────────────


def test_load_canon_real_fixtures_skip_dunder():
    canon = load_canon()
    assert "code-review.yml" in canon
    assert "__init__.py" not in canon
    # No canon template for pytest.yml yet (tracked as a follow-up, #1347's AC5
    # note) — but this no longer surfaces as a *planning*-layer canon gap: the
    # three python repos list it in custom_files (REPO_CUSTOM, never written) and
    # non-python repos derive an empty resolved_language_test_files, so the
    # missing canon entry is simply never looked up. See
    # test_plan_account_pass_no_pytest_canon_gap below.
    assert "pytest.yml" not in canon


@pytest.mark.parametrize("repo", BASELINE_REPOS)
def test_load_manifest_and_snapshot_round_trip(repo):
    manifest = load_manifest(repo)
    snapshot = load_snapshot(repo)
    assert manifest.repo == repo
    assert snapshot.repo == repo


def test_load_manifest_raises_on_non_mapping_yaml(tmp_path):
    # A list/scalar-shaped manifest is a defect, not an empty manifest — the
    # loader must raise rather than silently constructing an empty Manifest.
    (tmp_path / "Osasuwu__test.manifest.yml").write_text("- item1\n- item2")
    with pytest.raises(ApplyError, match="not a mapping"):
        load_manifest("Osasuwu/test", manifests_dir=tmp_path)


def test_load_manifest_empty_document_is_empty_manifest(tmp_path):
    # An empty YAML document (None) is a valid empty manifest, not an error.
    (tmp_path / "Osasuwu__test.manifest.yml").write_text("")
    manifest = load_manifest("Osasuwu/test", manifests_dir=tmp_path)
    assert manifest.repo == ""


# ── per-account-pass orchestrator (dry-run) ───────────────────────────


def test_plan_account_pass_reports_per_repo():
    plans = plan_account_pass(BASELINE_REPOS)
    assert [p.repo for p in plans] == BASELINE_REPOS
    assert all(isinstance(p, RepoPlan) for p in plans)


def test_plan_account_pass_no_pytest_canon_gap():
    # AC6: the REPO_CUSTOM write-filter (AC2/AC5, pytest.yml lives in custom_files
    # for the python repos) plus the derived ci_language axis (AC3/AC4,
    # non-python repos resolve an empty language_test_files) mean pytest.yml is
    # never planned as a WRITE_FILE or DELETE_FILE anywhere in the account pass —
    # every repo comes back applyable, not fenced off by a canon gap.
    plans = plan_account_pass(BASELINE_REPOS)
    summary = summarize_account_pass(plans)
    assert summary["total"] == len(BASELINE_REPOS)
    assert summary["gapped"] == 0
    assert summary["errored"] == 0
    assert summary["applyable"] == len(BASELINE_REPOS)
    for p in plans:
        assert p.canon_gaps == []
        assert p.applyable
        assert not p.gapped
        for call in p.calls:
            assert call.path != ".github/workflows/pytest.yml"
    assert any(p.calls for p in plans), (
        "expected at least one repo to plan a non-empty call sequence"
    )


def test_plan_account_pass_injected_canon_makes_repo_applyable():
    # With a canon map that covers every planned write (including pytest.yml),
    # a repo translates to a non-empty call sequence and no gaps.
    full_canon = dict(load_canon())
    full_canon["pytest.yml"] = "name: pytest on {{ runs_on }}\n"
    plans = plan_account_pass(BASELINE_REPOS, canon=full_canon)
    assert any(p.calls and not p.canon_gaps for p in plans)
    for p in plans:
        assert p.canon_gaps == []
        assert p.error is None


def test_committed_canon_dependabot_targets_the_live_default_branch():
    """#1406 — the *shipped* canon, not just a test template, must take its
    ``target-branch`` from the audit. redrobot's default branch is ``master``;
    canon hardcoded ``main``, so every dependabot PR it wrote there targeted a
    branch that does not exist.
    """
    applier = Applier(
        _manifest(repo="second-owner/other-repo"),
        load_canon(),
        snapshot=_snap({}, repo="second-owner/other-repo", default_branch="master"),
    )

    rendered = applier.render_content(".github/dependabot.yml")

    assert "target-branch: main" not in rendered
    assert rendered.count("target-branch: master") == 2


def test_committed_canon_ci_meta_push_trigger_targets_the_live_default_branch():
    """Sibling of the dependabot bug found by grepping canon for hardcoded
    ``main`` (#1406): ci-meta's ``push:`` trigger named a branch the target repo does
    not have, so the workflow never ran on merges there.
    """
    applier = Applier(
        _manifest(repo="second-owner/other-repo"),
        load_canon(),
        snapshot=_snap({}, repo="second-owner/other-repo", default_branch="master"),
    )

    rendered = applier.render_content(".github/workflows/ci-meta.yml")

    assert "branches: [main]" not in rendered
    assert "branches: [master]" in rendered


def test_ci_meta_is_not_written_to_a_repo_without_tests_ci():
    """#1406 — ci-meta.yml runs ``pytest tests/ci/``, which exits 4 (usage error)
    when that directory does not exist. Writing it into such a repo manufactures
    a permanently red required check, so an observed absence must skip the file
    rather than ship a broken gate.
    """
    plan = [
        Action(
            kind=ActionKind.WRITE_FILE,
            path=".github/workflows/ci-meta.yml",
            file_class="managed",
        ),
        Action(
            kind=ActionKind.WRITE_FILE,
            path=".github/workflows/code-review.yml",
            file_class="managed",
        ),
    ]
    applier = Applier(_manifest(), _TEST_CANON, snapshot=_snap({}, has_tests_ci=False))

    calls = applier.translate(plan, ActualState())

    assert [c.path for c in calls] == [".github/workflows/code-review.yml"]
    assert any("tests/ci" in n for n in applier.observed_notes())


def test_ci_meta_is_written_when_tests_ci_is_present():
    """The skip is driven by a *positive* observation of absence, not by silence:
    an unobserved repo (older snapshot fixture) keeps the pre-#1406 behaviour.
    """
    plan = [
        Action(
            kind=ActionKind.WRITE_FILE,
            path=".github/workflows/ci-meta.yml",
            file_class="managed",
        )
    ]

    for snapshot in (_snap({}, has_tests_ci=True), _snap({})):
        applier = Applier(_manifest(), _TEST_CANON, snapshot=snapshot)
        calls = applier.translate(plan, ActualState())
        assert [c.path for c in calls] == [".github/workflows/ci-meta.yml"]
        assert not any("tests/ci" in n for n in applier.observed_notes())


def test_committed_canon_ci_meta_pytest_args_default_to_nothing():
    """#1406 — the extra-args axis is opt-in: a repo that says nothing gets the
    plain invocation it has always had, byte-for-byte. Anything else would
    rewrite ci-meta.yml in every repo on the next sync for no reason.
    """
    applier = Applier(_manifest(), load_canon(), snapshot=_snap({}))

    rendered = applier.render_content(".github/workflows/ci-meta.yml")

    assert "run: pytest tests/ci/ -v\n" in rendered



def test_plan_account_pass_isolates_a_bad_repo(tmp_path):
    # A malformed manifest for ONE repo must surface as that repo's `error`
    # without discarding the rest of the account pass (PRD story 9).
    bad = "Osasuwu/jarvis"
    (tmp_path / f"{bad.replace('/', '__')}.manifest.yml").write_text("- not-a-mapping")
    plans = {p.repo: p for p in plan_account_pass([bad], manifests_dir=tmp_path)}
    assert plans[bad].error is not None
    assert "not a mapping" in plans[bad].error
    assert plans[bad].calls == []


def test_plan_account_pass_empty_repo_list():
    assert plan_account_pass([]) == []


def test_plan_account_pass_is_idempotent_against_synced_state():
    # A repo whose actual state already carries every rendered managed body and
    # the required contexts must translate to an empty call sequence — a re-run
    # on a fully-synced repo is a no-op (PRD story 23).
    full_canon = dict(load_canon())
    full_canon["pytest.yml"] = "name: pytest\n"
    repo = BASELINE_REPOS[0]
    manifest = load_manifest(repo)
    # A snapshot is mandatory to render canon at all now that the observed axes
    # have no manifest-side fallback (#1406).
    applier = Applier(manifest, full_canon, snapshot=_snap({}, repo=repo))
    plan = Planner(manifest).plan(ActualState())
    # Build an actual state that is byte-identical to what the plan would write.
    synced_files = {}
    contexts = []
    for action in plan:
        if action.kind == ActionKind.WRITE_FILE:
            synced_files[action.path] = _hash(applier.render_content(action.path))
        elif action.kind == ActionKind.SET_CHECK_CONTEXTS:
            contexts = list(action.context_names)
    actual = ActualState(files=synced_files, required_check_contexts=contexts)
    assert applier.translate(plan, actual) == []
