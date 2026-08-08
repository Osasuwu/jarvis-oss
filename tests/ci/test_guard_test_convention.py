"""Carrier-2 gate for the path-filtered-guard convention (#326, mechanized by #1418).

The rule, from `CLAUDE.md`: any workflow under `.github/workflows/` with a `paths:`
filter **that blocks PRs** must ship a co-located meta-test under `tests/ci/`, and
that meta-test must name every path pattern the filter uses.

Why it needs a mechanical carrier at all — a `paths:` filter that stops matching
does **not** error. The workflow silently never runs, and "did not run" is
indistinguishable from "ran and passed" on the PR checks page. The convention
existed as prose from #326 onward and was violated anyway; #1418 moved the
rationale to `docs/reference/ci-guard-meta-tests.md` and put the enforcement here,
where it costs zero always-loaded bytes and cannot be skipped.

**"That blocks PRs" is the load-bearing qualifier**, and it is why the scope below
is the `pull_request` / `pull_request_target` triggers only. A filter on `push`
gates nothing a PR waits on: if it drifts, the cost is a wasted or skipped
post-merge run, not a check that reads green because it never ran. `gitleaks.yml`
is the live example — its `push` trigger is filtered on purpose while its
`pull_request` trigger is deliberately unconditional (see `TestGitleaksPrTrigger`).

Naming: workflow `foo-bar.yml` is served by `tests/ci/test_foo_bar.py` or
`tests/ci/test_foo_bar_guard.py`. Where a meta-test legitimately covers a workflow
under a different name, declare it in `WORKFLOW_TEST_OVERRIDES` rather than
loosening the derivation — the override list is the reviewable record of every
exception.

Note the shape of `TestDetector`. No workflow in this repo currently filters a
PR-blocking trigger, so the enforcement tests parametrize over an empty list. A
guard that is green because it found nothing to check is the same failure mode it
exists to prevent, so the detector is separately pinned against synthetic
workflows that DO carry filters.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_TESTS_DIR = REPO_ROOT / "tests" / "ci"

#: Triggers whose runs a PR's checks page waits on. A filter here can make a
#: required check silently never report; a filter on any other trigger cannot.
PR_BLOCKING_TRIGGERS = ("pull_request", "pull_request_target")

#: Workflow stem -> meta-test filename, for cases where the derived name is wrong.
#: Keep this short and justified; it is the exception record, not a catch-all.
WORKFLOW_TEST_OVERRIDES: dict[str, str] = {
    # `schema-drift-check.yml`'s meta-test predates the naming convention.
    "schema-drift-check": "test_schema_drift_guard.py",
}


def _on_block(doc: dict) -> dict:
    """Return a workflow's trigger block.

    YAML 1.1 parses a bare `on:` key as the boolean ``True``, which is why this
    cannot just be ``doc["on"]``. The quoted spelling is accepted too, so a
    `"on":` does not slip past the detector.
    """
    for key in (True, "on", "On", "ON"):
        block = doc.get(key)
        if isinstance(block, dict):
            return block
    return {}


def path_filters(doc: dict, triggers: tuple[str, ...] = PR_BLOCKING_TRIGGERS) -> list[str]:
    """Every `paths:`/`paths-ignore:` pattern under the given triggers.

    Defaults to the PR-blocking triggers, which is the scope of the convention.
    Empty means the workflow is unfiltered where it matters and is out of scope.
    """
    patterns: list[str] = []
    on = _on_block(doc)
    for name in triggers:
        trigger = on.get(name)
        if not isinstance(trigger, dict):
            continue
        for key in ("paths", "paths-ignore"):
            value = trigger.get(key)
            if isinstance(value, list):
                patterns.extend(str(v) for v in value)
            elif isinstance(value, str):
                patterns.append(value)
    return patterns


def candidate_test_names(workflow_stem: str) -> list[str]:
    """Accepted meta-test filenames for a workflow, most specific first."""
    if workflow_stem in WORKFLOW_TEST_OVERRIDES:
        return [WORKFLOW_TEST_OVERRIDES[workflow_stem]]
    slug = workflow_stem.replace("-", "_")
    return [f"test_{slug}_guard.py", f"test_{slug}.py"]


def _workflow_docs() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    paths = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    for wf in paths:
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            out.append((wf, doc))
    return out


FILTERED = [(wf, p) for wf, doc in _workflow_docs() if (p := path_filters(doc))]


class TestPathFilteredWorkflowsHaveMetaTests:
    def test_workflows_dir_is_populated(self):
        """Guard the guard: an empty glob would make every test below vacuous."""
        assert len(_workflow_docs()) >= 10, (
            f"expected the workflow corpus under {WORKFLOWS_DIR}; found "
            f"{len(_workflow_docs())} parseable files"
        )

    @pytest.mark.parametrize(
        "workflow,patterns", FILTERED, ids=[wf.name for wf, _ in FILTERED]
    )
    def test_meta_test_exists(self, workflow: Path, patterns: list[str]):
        names = candidate_test_names(workflow.stem)
        found = [n for n in names if (CI_TESTS_DIR / n).exists()]
        assert found, (
            f".github/workflows/{workflow.name} path-filters a PR-blocking trigger "
            f"({patterns}) but has no co-located meta-test. Expected one of {names} "
            "under tests/ci/. A paths: filter that stops matching does not error - "
            "the workflow silently never runs, and on the checks page that is "
            "indistinguishable from passing (#326). Add the meta-test, or register a "
            "different filename in WORKFLOW_TEST_OVERRIDES. Rationale: "
            "docs/reference/ci-guard-meta-tests.md"
        )

    @pytest.mark.parametrize(
        "workflow,patterns", FILTERED, ids=[wf.name for wf, _ in FILTERED]
    )
    def test_meta_test_names_the_filtered_paths(self, workflow: Path, patterns: list[str]):
        """The 'config' half of the convention: the test pins the canonical path.

        A meta-test that exercises the workflow's decision rule but never mentions
        the path it is filtered on cannot catch the filter drifting off that path,
        which is the specific failure #326 was opened for.
        """
        names = candidate_test_names(workflow.stem)
        found = next((n for n in names if (CI_TESTS_DIR / n).exists()), None)
        if found is None:
            pytest.skip("covered by test_meta_test_exists")
        text = (CI_TESTS_DIR / found).read_text(encoding="utf-8")
        missing = [p for p in patterns if p not in text]
        assert not missing, (
            f"tests/ci/{found} guards .github/workflows/{workflow.name}, whose "
            f"filter names {patterns}, but the test never mentions {missing}. Assert "
            "the canonical path literally so the test fails when the filter drifts "
            "off it, not only when the decision rule changes. Rationale: "
            "docs/reference/ci-guard-meta-tests.md"
        )


class TestGitleaksPrTrigger:
    """`gitleaks.yml` is the reason this module scopes to PR-blocking triggers.

    `Detect secrets with gitleaks` is a REQUIRED check. A required check that is
    path-filtered on `pull_request` does not fail — GitHub parks the context at
    "Expected - waiting for status" and the PR is blocked forever with every other
    gate green (this stalled PR #894; decision da8b97e4). Its `push` filter is
    deliberate and stays. Pin both halves so a well-meaning "let's not scan doc
    PRs" edit re-creates the stall in review instead of on a live PR.
    """

    @pytest.fixture(scope="class")
    def doc(self):
        return yaml.safe_load((WORKFLOWS_DIR / "gitleaks.yml").read_text(encoding="utf-8"))

    def test_pull_request_trigger_is_unfiltered(self, doc):
        assert path_filters(doc) == [], (
            "gitleaks.yml must not path-filter its pull_request trigger: it backs a "
            "REQUIRED check, and a skipped run parks the context at 'Expected' "
            "forever rather than reporting (PR #894)."
        )

    def test_push_trigger_keeps_its_filter(self, doc):
        assert path_filters(doc, ("push",)), (
            "gitleaks.yml's push filter is deliberate — a secret scan on every doc "
            "commit to main is wasted minutes, and push gates nothing."
        )


class TestDetector:
    """Pin the detector against synthetic workflows.

    The enforcement tests above are currently parametrized over an empty list. If
    the detector silently stopped detecting, they would stay green for exactly the
    reason a dead `paths:` filter stays green.
    """

    def test_detects_paths_on_bare_on_key(self):
        doc = yaml.safe_load(
            "on:\n  pull_request:\n    paths:\n      - 'mcp-memory/schema.sql'\n"
        )
        assert path_filters(doc) == ["mcp-memory/schema.sql"]

    def test_detects_paths_on_quoted_on_key(self):
        doc = yaml.safe_load('"on":\n  pull_request:\n    paths:\n      - src/**\n')
        assert path_filters(doc) == ["src/**"]

    def test_detects_paths_ignore(self):
        doc = yaml.safe_load("on:\n  pull_request:\n    paths-ignore:\n      - docs/**\n")
        assert path_filters(doc) == ["docs/**"]

    def test_detects_pull_request_target(self):
        doc = yaml.safe_load("on:\n  pull_request_target:\n    paths:\n      - a.py\n")
        assert path_filters(doc) == ["a.py"]

    def test_collects_across_both_pr_triggers(self):
        doc = yaml.safe_load(
            "on:\n"
            "  pull_request:\n    paths:\n      - a.py\n"
            "  pull_request_target:\n    paths:\n      - b.py\n"
        )
        assert sorted(path_filters(doc)) == ["a.py", "b.py"]

    def test_push_only_filter_is_out_of_scope(self):
        """The gitleaks shape: filtered where it gates nothing, open where it does."""
        doc = yaml.safe_load(
            "on:\n  pull_request:\n  push:\n    paths-ignore:\n      - docs/**\n"
        )
        assert path_filters(doc) == []
        assert path_filters(doc, ("push",)) == ["docs/**"]

    def test_unfiltered_pr_trigger_is_out_of_scope(self):
        doc = yaml.safe_load("on:\n  pull_request:\n    branches: [main]\n")
        assert path_filters(doc) == []

    def test_list_shorthand_trigger_is_not_a_filter(self):
        """`on: [push, pull_request]` parses triggers as strings, not dicts."""
        assert path_filters(yaml.safe_load("on: [push, pull_request]\n")) == []

    def test_derived_test_names(self):
        assert candidate_test_names("pr-body-check") == [
            "test_pr_body_check_guard.py",
            "test_pr_body_check.py",
        ]

    def test_override_wins_over_derivation(self):
        assert candidate_test_names("schema-drift-check") == ["test_schema_drift_guard.py"]

    def test_every_override_target_exists(self):
        """A stale override silently re-admits the workflow it claims to cover."""
        for stem, name in WORKFLOW_TEST_OVERRIDES.items():
            assert (CI_TESTS_DIR / name).exists(), (
                f"WORKFLOW_TEST_OVERRIDES maps '{stem}' to tests/ci/{name}, "
                "which does not exist"
            )
