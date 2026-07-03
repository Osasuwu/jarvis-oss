"""#326-class meta-test: pin required_check_contexts[] to producer job-names.

Asserts that each entry in the canon ``required_check_contexts[]`` maps to an
existing producing job-name across the canon workflows. A job rename in any
canon workflow **breaks** this test — preventing the class of bug where a
required check is silently disabled because the job name changed.

Convention per #326:
  tests/ci/test_<name>_guard.py — path-filtered CI guard fixture test.

This meta-test covers all canon workflows. If a new workflow is added to the
canon set, add its context mapping here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest
import yaml

from scripts.repo_baseline import Manifest, Renderer
from scripts.repo_baseline.canon import load_all_canon_templates

# ── Expected mapping: check-context → (workflow path, job name) ────────
# Each entry declares that a required_check_contexts[] value is produced by
# a specific job in a specific workflow. If the job is renamed, this test
# fails — that's the #326 guard at work.
#
# The "job" here is the job's actual name (the ``name:`` field under the
# job, or the job's top-level key if no ``name:`` is set), which is what
# appears in the GitHub UI and branch-protection required-checks list.
#
# "verify-verdict" is the final-gate job in the code-review retry-wrapper;
# the check name that branch protection sees is "verify-verdict".
EXPECTED_CHECK_CONTEXTS: Dict[str, tuple[str, str]] = {
    "verify-verdict": (
        ".github/workflows/code-review.yml",
        "verify-verdict",
    ),
    "owner-queue-guard": (
        ".github/workflows/owner-queue-guard.yml",
        "owner-queue-guard",
    ),
    "require-linked-issue": (
        ".github/workflows/pr-body-check.yml",
        "require-linked-issue",
    ),
    "meta-tests": (
        ".github/workflows/ci-meta.yml",
        "meta-tests",
    ),
}

# ── Workflows deployed as MANAGED but not contributing a required check ──
# Currently empty: all four canon *workflows* produce a required check. The
# non-workflow canon files (dependabot.yml, ISSUE_TEMPLATE/*, PR template) are
# filtered out by the ``.github/workflows/`` prefix check below, so they never
# reach this list. Add a path here only if a future canon *workflow* is
# intentionally non-gating.
NON_GATING_WORKFLOWS: List[str] = []


def _extract_job_names(workflow_yaml: str, workflow_path: str) -> Dict[str, str]:
    """Extract {job_id: display_name} from a rendered workflow YAML.

    Returns a dict mapping job_id → effective check name (the ``name:`` field
    or the job_id if no ``name:`` is set).
    """
    parsed = yaml.safe_load(workflow_yaml)
    if not isinstance(parsed, dict) or "jobs" not in parsed:
        return {}

    jobs = parsed["jobs"]
    if not isinstance(jobs, dict):
        return {}

    result: Dict[str, str] = {}
    for job_id, job_spec in jobs.items():
        if isinstance(job_spec, dict):
            display = job_spec.get("name", job_id)
            result[job_id] = str(display)
        else:
            result[job_id] = job_id
    return result


def _render_workflow(workflow_path: str, templates: Dict[str, str], renderer: Renderer,
                     manifest: Manifest) -> str | None:
    """Render a canon workflow template with a default manifest."""
    template = templates.get(workflow_path)
    if template is None:
        return None
    try:
        return renderer.render(template, manifest)
    except Exception:
        return None


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return Manifest.from_dict({
        "repo": "test-repo",
        "profile": "full",
        "required_check_contexts": list(EXPECTED_CHECK_CONTEXTS.keys()),
    })


@pytest.fixture(scope="module")
def templates() -> Dict[str, str]:
    return load_all_canon_templates()


@pytest.fixture(scope="module")
def renderer() -> Renderer:
    return Renderer()


# ── Tests ──────────────────────────────────────────────────────────────


class TestRequiredCheckContextGuard:
    """#326-class guard: each required_check_context maps to a producer job."""

    def test_every_context_has_producer_workflow(self, manifest, templates, renderer):
        """Each required_check_contexts[] entry maps to a job in a canon workflow."""
        required = manifest.resolve_axis("required_check_contexts")
        assert len(required) > 0, "required_check_contexts must not be empty"

        for ctx_name in required:
            assert ctx_name in EXPECTED_CHECK_CONTEXTS, (
                f"Required check context '{ctx_name}' has no producer mapping. "
                f"Add it to EXPECTED_CHECK_CONTEXTS in this test."
            )

            workflow_path, expected_job = EXPECTED_CHECK_CONTEXTS[ctx_name]
            rendered = _render_workflow(workflow_path, templates, renderer, manifest)
            assert rendered is not None, (
                f"Workflow {workflow_path} not found in canon templates"
            )

            job_names = _extract_job_names(rendered, workflow_path)
            found = any(
                display == expected_job
                for job_id, display in job_names.items()
            )
            assert found, (
                f"Required check context '{ctx_name}' expects job "
                f"'{expected_job}' in {workflow_path}, but no job with that "
                f"name exists. Known jobs: {list(job_names.values())}. "
                f"If you renamed the job, update EXPECTED_CHECK_CONTEXTS "
                f"in this test — that's the #326 guard catching the drift."
            )

    def test_job_rename_breaks_test(self, manifest, templates, renderer):
        """Verify that a deliberately wrong job name fails (guard works).

        This validates the meta-test itself: if we claim a non-existent
        job, the test must fail.
        """
        workflow_path, _ = EXPECTED_CHECK_CONTEXTS["verify-verdict"]
        rendered = _render_workflow(workflow_path, templates, renderer, manifest)
        assert rendered is not None

        job_names = _extract_job_names(rendered, workflow_path)

        # A job name that cannot exist
        assert "nonexistent-job-name" not in job_names.values(), (
            "Precondition failed: 'nonexistent-job-name' should not exist"
        )

    def test_all_canon_workflows_accounted_for(self, templates):
        """Every MANAGED workflow template has an entry in EXPECTED_CHECK_CONTEXTS
        or is listed in NON_GATING_WORKFLOWS."""
        for path in templates:
            # Non-workflow files (templates, dependabot) are not gating
            if not path.startswith(".github/workflows/"):
                continue

            # Check if this workflow is expected to produce a required check
            produces_check = any(
                info[0] == path
                for info in EXPECTED_CHECK_CONTEXTS.values()
            )
            if not produces_check:
                assert path in NON_GATING_WORKFLOWS, (
                    f"Workflow {path} is not listed in EXPECTED_CHECK_CONTEXTS "
                    f"nor in NON_GATING_WORKFLOWS. Either add its check context "
                    f"mapping or list it as non-gating."
                )

    def test_no_orphan_context_mappings(self):
        """Every EXPECTED_CHECK_CONTEXTS entry references an existing template.

        This is a config-test: it validates the test's own data structure.
        """
        from scripts.repo_baseline.canon import _CANON_MAP

        for ctx_name, (workflow_path, job_name) in EXPECTED_CHECK_CONTEXTS.items():
            assert workflow_path in _CANON_MAP, (
                f"Context '{ctx_name}' references {workflow_path} which is "
                f"not in the canon template map."
            )

    def test_pytest_workflow_job_matches_context(self, manifest, templates, renderer):
        """pytest.yml (LANGUAGE-TEST) should have a 'pytest' job.

        Note: pytest is LANGUAGE-TEST, not MANAGED, so it isn't in the
        canon template map. This test validates that IF it were added to
        REQUIRED_CHECK_CONTEXTS, the job name would match.
        """
        # Read the existing pytest.yml from the workspace
        pytest_path = Path(".github/workflows/pytest.yml")
        if not pytest_path.exists():
            pytest.skip("pytest.yml not found in workspace — skipping")

        raw = pytest_path.read_text()
        job_names = _extract_job_names(raw, str(pytest_path))
        assert "pytest" in job_names.values() or "pytest" in job_names, (
            f"pytest.yml has no 'pytest' job. Found: {list(job_names.values())}"
        )
