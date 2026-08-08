"""Tests for the Renderer axis-substitution."""

import pytest

from scripts.repo_baseline import Manifest, Renderer, RenderError


def _manifest(**overrides) -> Manifest:
    data = {
        "repo": "Osasuwu/jarvis",
        "profile": "full",
        "required_check_contexts": ["review"],
        **overrides,
    }
    return Manifest.from_dict(data)


# What the live audit supplies for the *observed* axes (#1406) — facts about the
# repo that no manifest may declare. The Applier builds this from the snapshot;
# pure-Renderer tests stand in for it here.
_OBSERVED = {"default_branch": "main"}


class TestBasicSubstitution:
    def test_simple_axis(self):
        renderer = Renderer()
        result = renderer.render("runs-on: {{ runs_on }}", _manifest())
        assert "[ubuntu-latest]" in result  # inline YAML array

    def test_list_axis(self):
        """List axes produce inline YAML arrays."""
        renderer = Renderer()
        result = renderer.render(
            "contexts: {{ required_check_contexts }}",
            _manifest(required_check_contexts=["review", "pytest"]),
        )
        assert "[review, pytest]" in result

    def test_multiple_axes(self):
        renderer = Renderer()
        result = renderer.render(
            "lang={{ ci_language }} marketplace={{ code_review_marketplace }}",
            _manifest(),
        )
        assert "lang=python" in result
        assert "marketplace=anthropics/claude-code-action@v1" in result

    def test_unknown_axis_raises(self):
        """An axis with no profile default and not set in manifest raises error."""
        renderer = Renderer()
        with pytest.raises(RenderError, match="Axis 'nonexistent_axis' is required"):
            renderer.render("{{ nonexistent_axis }}", _manifest())


class TestCanonTemplates:
    def test_code_review_template_renders(self):
        """The code-review.yml canon template renders without errors."""
        from scripts.repo_baseline.canon import load_canon_template

        template = load_canon_template(".github/workflows/code-review.yml")
        assert template is not None

        renderer = Renderer()
        result = renderer.render(template, _manifest())
        assert "Code Review" in result
        assert "attempt-1" in result
        assert "attempt-2" in result
        assert "verify-verdict" in result
        assert "anthropics/claude-code-action@v1" in result
        assert "ubuntu-latest" in result

    def test_owner_queue_guard_template_renders(self):
        from scripts.repo_baseline.canon import load_canon_template

        template = load_canon_template(".github/workflows/owner-queue-guard.yml")
        assert template is not None

        result = Renderer().render(template, _manifest())
        assert "Owner Queue Guard" in result
        assert "ubuntu-latest" in result

    def test_pr_body_check_template_renders(self):
        from scripts.repo_baseline.canon import load_canon_template

        template = load_canon_template(".github/workflows/pr-body-check.yml")
        assert template is not None

        result = Renderer().render(template, _manifest())
        assert "PR Body Check" in result

    def test_ci_meta_template_renders(self):
        from scripts.repo_baseline.canon import load_canon_template

        template = load_canon_template(".github/workflows/ci-meta.yml")
        assert template is not None

        result = Renderer().render(template, _manifest(), _OBSERVED)
        assert "CI Meta" in result

    def test_dependabot_template_renders(self):
        from scripts.repo_baseline.canon import load_canon_template

        template = load_canon_template(".github/dependabot.yml")
        assert template is not None

        result = Renderer().render(template, _manifest(), _OBSERVED)
        assert "version: 2" in result
        assert "pip" in result

    def test_issue_templates_render(self):
        from scripts.repo_baseline.canon import load_canon_template

        for path in [
            ".github/ISSUE_TEMPLATE/bug.yml",
            ".github/ISSUE_TEMPLATE/task.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
        ]:
            template = load_canon_template(path)
            assert template is not None, f"Template {path} should load"
            result = Renderer().render(template, _manifest())
            assert len(result) > 0

    def test_all_canon_templates_render_without_error(self):
        """Every canon template renders successfully with a full-profile manifest.

        Validates that no canon-style ``{{ axis }}`` placeholders remain in the
        output -- ``${{ secrets.X }}`` GitHub expressions are preserved as-is.
        """
        from scripts.repo_baseline.canon import load_all_canon_templates

        templates = load_all_canon_templates()
        assert len(templates) > 0
        renderer = Renderer()
        manifest = _manifest()
        import re

        canon_pattern = re.compile(r"\{\{\s*\w+\s*\}\}")  # matches {{ axis_name }}

        for path, template in templates.items():
            result = renderer.render(template, manifest, _OBSERVED)
            remaining = canon_pattern.findall(result)
            assert not remaining, (
                f"Template {path} has unsubstituted canon placeholders: {remaining}"
            )

    def test_no_canon_placeholder_sits_inside_a_comment_line(self):
        """A ``{{ axis }}`` in explanatory prose gets substituted like any other
        (#1406), which silently turns a general statement into a repo-specific
        one: dependabot.yml's comment "``{{ default_branch }}`` is an observed
        axis" shipped to redrobot reading "master is an observed axis". The
        comment is documentation for the *next* reader of the canon file, so it
        must survive rendering unchanged.

        Scoped to the canon pattern (``\\w+`` only), so GitHub Actions
        expressions like ``${{ github.job }}`` — which carry a dot and are never
        substituted — stay legal in comments.
        """
        import re

        from scripts.repo_baseline.canon import load_all_canon_templates

        canon_pattern = re.compile(r"\{\{\s*\w+\s*\}\}")
        offenders = [
            f"{path}:{i}: {line.strip()}"
            for path, template in load_all_canon_templates().items()
            for i, line in enumerate(template.splitlines(), 1)
            if line.strip().startswith("#") and canon_pattern.search(line)
        ]
        assert not offenders, "canon placeholders in comment prose: " + "; ".join(offenders)

    def test_default_branch_without_an_observation_fails_loudly(self):
        """#1406 — ``default_branch`` deliberately has NO profile default, unlike
        ``runs_on``. A repo always has a default branch, so "nothing observed"
        means the caller skipped the audit, not that the repo lacks the fact —
        and a plausible-looking ``main`` is exactly the silent wrong answer that
        aimed redrobot's dependabot PRs at a non-existent branch. Rendering
        without the observation must raise rather than guess.
        """
        with pytest.raises(RenderError, match="default_branch"):
            Renderer().render("target-branch: {{ default_branch }}", _manifest())


class TestErrorHandling:
    def test_missing_required_axis_raises(self):
        """Omitting a required axis (not set, no profile default) raises."""
        renderer = Renderer()
        # 'nonexistent' has no value anywhere
        with pytest.raises(RenderError):
            renderer.render("{{ nonexistent }}", _manifest())

    def test_render_error_messages_include_axis_name(self):
        renderer = Renderer()
        try:
            renderer.render("{{ bad_axis }}", _manifest())
        except RenderError as e:
            assert "bad_axis" in str(e)
