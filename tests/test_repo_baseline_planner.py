"""Tests for the Planner 3-class routing and action-plan generation.

Covers:
- 3-class routing: MANAGED overwrite, REPO-CUSTOM default-deny,
  LANGUAGE-TEST axis application.
- Ordering invariant: MANAGED/LANGUAGE-TEST before SET_CHECK_CONTEXTS.
- classify_file() for individual path lookup.
"""

from scripts.repo_baseline import FileClass, Manifest, Planner
from scripts.repo_baseline.planner import ActionKind, ActualState

# ── Helpers ────────────────────────────────────────────────────────────


def _manifest(**overrides) -> Manifest:
    data = {
        "repo": "test-repo",
        "profile": "full",
        "required_check_contexts": ["review", "pytest", "meta-tests"],
        **overrides,
    }
    return Manifest.from_dict(data)


def _plan(manifest: Manifest) -> list:
    return Planner(manifest).plan(ActualState())


# ─── 3-class routing ───────────────────────────────────────────────────


class Test3ClassRouting:
    def test_managed_file_gets_write_action(self):
        """MANAGED files generate WRITE_FILE actions."""
        actions = _plan(_manifest())
        code_review_actions = [a for a in actions
                               if a.path == ".github/workflows/code-review.yml"]
        assert len(code_review_actions) == 1
        a = code_review_actions[0]
        assert a.kind == ActionKind.WRITE_FILE
        assert a.file_class == FileClass.MANAGED.value

    def test_repo_custom_file_untouched_by_default(self):
        """REPO-CUSTOM files are default-deny — no action unless in custom_files."""
        manifest = _manifest()
        fclass = manifest.class_for_file("scripts/some_custom_script.py")
        assert fclass == FileClass.REPO_CUSTOM

        # No action should exist for a non-listed custom file
        actions = _plan(manifest)
        custom_actions = [a for a in actions
                          if a.path == "scripts/some_custom_script.py"]
        assert len(custom_actions) == 0

    def test_repo_custom_file_in_allowlist_is_preserved(self):
        """A file in custom_files[] is classified REPO_CUSTOM."""
        manifest = _manifest(custom_files=["scripts/deploy.sh"])
        fclass = manifest.class_for_file("scripts/deploy.sh")
        assert fclass == FileClass.REPO_CUSTOM

    def test_language_test_file_gets_write_action(self):
        """LANGUAGE-TEST files generate WRITE_FILE actions with language_test class."""
        actions = _plan(_manifest())
        pytest_actions = [a for a in actions
                          if a.path == ".github/workflows/pytest.yml"]
        assert len(pytest_actions) == 1
        a = pytest_actions[0]
        assert a.kind == ActionKind.WRITE_FILE
        assert a.file_class == FileClass.LANGUAGE_TEST.value

    def test_unknown_file_is_repo_custom_default_deny(self):
        """A path in none of the file lists is REPO_CUSTOM (default-deny)."""
        manifest = _manifest()
        assert manifest.class_for_file(".github/random-unknown.yml") == FileClass.REPO_CUSTOM


class TestPlannerOrdering:
    def test_file_writes_before_check_contexts(self):
        """Ordering invariant: WRITE_FILE before SET_CHECK_CONTEXTS."""
        actions = _plan(_manifest())
        seen_writes = False
        seen_contexts = False
        for a in actions:
            if a.kind == ActionKind.WRITE_FILE:
                seen_writes = True
            if a.kind == ActionKind.SET_CHECK_CONTEXTS:
                seen_contexts = True
                assert seen_writes, "SET_CHECK_CONTEXTS must come after WRITE_FILE"

        assert seen_writes, "Expected at least one WRITE_FILE action"
        assert seen_contexts, "Expected SET_CHECK_CONTEXTS action"

    def test_check_contexts_contains_required_names(self):
        """SET_CHECK_CONTEXTS carries the manifest's required_check_contexts."""
        manifest = _manifest(required_check_contexts=[
            "review",
            "pytest",
            "owner-queue-guard",
        ])
        actions = _plan(manifest)
        ctx_actions = [a for a in actions
                       if a.kind == ActionKind.SET_CHECK_CONTEXTS]
        assert len(ctx_actions) == 1
        assert ctx_actions[0].context_names == [
            "review",
            "pytest",
            "owner-queue-guard",
        ]

    def test_minimal_profile_has_fewer_managed_files(self):
        """'minimal' profile ships fewer managed files than 'full'."""
        full = _manifest(profile="full")
        minimal = _manifest(profile="minimal")
        full_actions = _plan(full)
        minimal_actions = _plan(minimal)
        full_write_count = sum(1 for a in full_actions
                               if a.kind == ActionKind.WRITE_FILE)
        minimal_write_count = sum(1 for a in minimal_actions
                                  if a.kind == ActionKind.WRITE_FILE)
        assert minimal_write_count < full_write_count


class TestSetCheckContexts:
    def test_no_check_contexts_emitted_when_empty(self):
        """When required_check_contexts is empty (default), no SET_CHECK_CONTEXTS action is emitted."""
        manifest = _manifest(required_check_contexts=[])
        actions = _plan(manifest)
        ctx_actions = [a for a in actions if a.kind == ActionKind.SET_CHECK_CONTEXTS]
        assert len(ctx_actions) == 0, (
            "SET_CHECK_CONTEXTS must not be emitted with empty required_check_contexts "
            "— doing so would clear all required status checks on deploy"
        )

    def test_no_check_contexts_for_undeclared_manifest(self):
        """A manifest that never declares required_check_contexts should not emit SET_CHECK_CONTEXTS."""
        manifest = Manifest.from_dict({"repo": "bare-repo", "profile": "full"})
        actions = Planner(manifest).plan(ActualState())
        ctx_actions = [a for a in actions if a.kind == ActionKind.SET_CHECK_CONTEXTS]
        assert len(ctx_actions) == 0

    def test_delete_file_for_unmanaged_actual_path(self):
        """Files in actual state but not in any managed set get DELETE_FILE actions."""
        actual = ActualState(files={".github/workflows/stale.yml": ""})
        actions = Planner(_manifest()).plan(actual)
        assert any(
            a.kind == ActionKind.DELETE_FILE and a.path == ".github/workflows/stale.yml"
            for a in actions
        )


class TestClassifyFile:
    def test_classify_managed(self):
        manifest = _manifest()
        assert Planner(manifest).classify_file(
            ".github/workflows/code-review.yml"
        ) == FileClass.MANAGED

    def test_classify_language_test(self):
        manifest = _manifest()
        assert Planner(manifest).classify_file(
            ".github/workflows/pytest.yml"
        ) == FileClass.LANGUAGE_TEST

    def test_classify_custom(self):
        manifest = _manifest(custom_files=["scripts/foo.py"])
        assert Planner(manifest).classify_file(
            "scripts/foo.py"
        ) == FileClass.REPO_CUSTOM
