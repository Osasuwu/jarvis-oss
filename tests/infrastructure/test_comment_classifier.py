"""Golden-case eval fixtures for ``comment_classifier``.

Tests the keep/remove judge against the rules in ``docs/deslop-standard.md``.
"""

from __future__ import annotations

import textwrap

from src.comment_classifier import classify, ClassifierContext


# ---------------------------------------------------------------------------
# Golden-case fixtures (from the issue AC — real comments in the repo)
# ---------------------------------------------------------------------------


class TestGoldenCases:
    """Five named golden cases from the issue body."""

    def test_import_guard_comment_keep_why(self):
        """``agents/poller.py:91`` — import-guard explaining *why* there is an
        import-time assertion.

        Comment::

            # Import-time guard: if task_queue adds a new terminal state the
            # poller must decide how to requeue it — fail fast rather than
            # silently stranding events.
        """
        comment = textwrap.dedent("""\
            # Import-time guard: if task_queue adds a new terminal state the
            # poller must decide how to requeue it — fail fast rather than
            # silently stranding events.
        """)
        assert classify(comment) == "keep_why"

    def test_restate_filter_by_project_remove(self):
        """``evals/run_evals.py:51`` — pure restate of obvious code.

        Comment: ``# Filter by project if specified``

        The code immediately below is ``if project is not None:
        query = query.eq("project", project)`` — the comment says exactly
        what the code does, adding zero information.
        """
        comment = "# Filter by project if specified"
        assert classify(comment) == "remove"

    def test_fail_open_safety_keep_warning(self):
        """``mcp-memory/write_scrubber.py:103`` — safety comment containing
        "fail-open".

        Comment excerpt: ``(path writes hard-blocked), not a secret leak
        (fail-open). Loud-but-alive beats dead.``
        """
        comment = (
            "# (path writes hard-blocked), not a secret leak (fail-open). "
            "Loud-but-alive beats dead."
        )
        assert classify(comment) == "keep_warning"

    def test_url_format_external_keep_external(self):
        """``agents/github_client.py:255`` — URL-format reference comment.

        Comment: ``# Extract PR number from URL like
        https://github.com/owner/repo/pull/999``
        """
        comment = (
            "# Extract PR number from URL like "
            "https://github.com/owner/repo/pull/999"
        )
        assert classify(comment) == "keep_external"

    def test_russian_fail_closed_keep_warning(self):
        """``trough_frame.py`` (redrobot) — Russian-language safety comment
        containing "fail-closed".
        """
        comment = "# fail-closed — не продолжаем при ошибке в guardrail"
        assert classify(comment) == "keep_warning"


# ---------------------------------------------------------------------------
# Disposition coverage — each of the five must be reachable
# ---------------------------------------------------------------------------


class TestDispositionCoverage:
    """Every disposition is returned by at least one test."""

    def test_remove_disp(self):
        assert classify("# Filter results") == "remove"

    def test_keep_why_disp(self):
        assert classify("# Must never return None") == "keep_why"

    def test_keep_external_disp(self):
        assert classify("# See https://example.com/docs") == "keep_external"

    def test_keep_warning_disp(self):
        assert classify("# guardrail: fail-closed") == "keep_warning"

    def test_keep_unsure_disp(self):
        """A comment with no matching signal → keep_unsure."""
        assert classify("# This was refactored last month") == "keep_unsure"


# ---------------------------------------------------------------------------
# Safety rule — keep_warning
# ---------------------------------------------------------------------------


class TestSafetyComments:
    def test_fail_open_with_hyphen(self):
        assert classify("# fail-open path") == "keep_warning"

    def test_fail_open_with_space(self):
        assert classify("# fail open path") == "keep_warning"

    def test_fail_closed_hyphenated(self):
        assert classify("# fail-closed guard") == "keep_warning"

    def test_fail_closed_no_hyphen(self):
        assert classify("# fail closed guard") == "keep_warning"

    def test_guardrail(self):
        assert classify("# guardrail active") == "keep_warning"

    def test_guardrail_in_sentence(self):
        assert classify("# This acts as a guardrail against") == "keep_warning"

    def test_swallow_error(self):
        assert classify("# Swallows the error to keep the loop alive") == "keep_warning"

    def test_mask_error(self):
        assert classify("# Masks the underlying error on purpose") == "keep_warning"

    def test_not_marked_as_safety(self):
        """A generic "fail" does not trigger safety — must match the full
        compound term.
        """
        assert classify("# This may fail silently") != "keep_warning"


# ---------------------------------------------------------------------------
# External-fact rule — keep_external
# ---------------------------------------------------------------------------


class TestExternalFactComments:
    def test_url(self):
        assert classify("# Docs at https://example.com/api") == "keep_external"

    def test_wire_format_keyword(self):
        assert classify("# Wire format is JSON") == "keep_external"

    def test_upstream_quirk(self):
        assert classify("# Upstream API returns null for empty") == "keep_external"

    def test_third_party_behavior(self):
        assert classify("# Third-party lib rounds silently") == "keep_external"


# ---------------------------------------------------------------------------
# WHY explanation — keep_why
# ---------------------------------------------------------------------------


class TestWhyComments:
    def test_because_keyword(self):
        assert classify("# Because the DB returns None") == "keep_why"

    def test_so_that(self):
        assert classify("# So that the caller can retry") == "keep_why"

    def test_otherwise_clause(self):
        assert classify("# Otherwise the pipeline stalls") == "keep_why"

    def test_ensure(self):
        assert classify("# Ensure the lock is released") == "keep_why"

    def test_prevent(self):
        assert classify("# Prevent double-spawn") == "keep_why"

    def test_must_not(self):
        assert classify("# Must not be called from async") == "keep_why"


# ---------------------------------------------------------------------------
# Pure restate — remove
# ---------------------------------------------------------------------------


class TestRestateComments:
    def test_filter_restate(self):
        assert classify("# Filter by name") == "remove"

    def test_check_restate(self):
        assert classify("# Check if file exists") == "remove"

    def test_validate_restate(self):
        assert classify("# Validate input") == "remove"

    def test_import_restate(self):
        assert classify("# Import config") == "remove"

    def test_convert_restate(self):
        assert classify("# Convert to int") == "remove"

    def test_load_restate(self):
        assert classify("# Load data from disk") == "remove"

    def test_save_restate(self):
        assert classify("# Save results") == "remove"

    def test_third_person_verb_restate(self):
        """Third-person forms (``Gets``, ``Validates``) must restate-match
        too — the verb-boundary regex previously required an exact-stem
        match and missed the trailing ``s``.
        """
        assert classify("# Gets the current user") == "remove"
        assert classify("# Validates the input") == "remove"


# ---------------------------------------------------------------------------
# Traceability — kept unless pure restate
# ---------------------------------------------------------------------------


class TestTraceability:
    def test_pure_restate_with_ref_is_kept(self):
        """A pure restate that also references an issue number is kept
        (conservative — the ref provides traceability).
        """
        assert classify("# Filter by project #123") != "remove"

    def test_ref_with_context_kept(self):
        """A comment that cites an issue number with additional context is
        kept.
        """
        assert classify("# Per #456, must never block") == "keep_why"

    def test_ref_alone_as_restate(self):
        """A comment that is only a restate verb + ref is kept at
        keep_unsure — the traceability ref prevents removal.
        """
        assert classify("# Validate #789") == "keep_unsure"


# ---------------------------------------------------------------------------
# Edge cases — blank, empty, malformed
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_comment(self):
        assert classify("") == "keep_unsure"

    def test_whitespace_only(self):
        assert classify("   ") == "keep_unsure"

    def test_just_hash(self):
        assert classify("#") == "keep_unsure"

    def test_multiline_comment(self):
        """Multi-line comments are joined and matched across lines."""
        comment = "# line one\n# line two\n# line three"
        assert classify(comment) == "keep_unsure"

    def test_multiline_with_url(self):
        """URL detection works across line boundaries."""
        comment = (
            "# See the docs\n"
            "# https://example.com\n"
            "# for details"
        )
        assert classify(comment) == "keep_external"

    def test_context_provided(self):
        """The context parameter is accepted and does not affect
        classification (the rule-based judge does not examine context
        for the initial implementation).
        """
        ctx = ClassifierContext(
            file_path="src/foo.py",
            preceding_code="x = 1\n",
            following_code="y = 2\n",
        )
        assert classify("# Some comment", context=ctx) == "keep_unsure"

    def test_safety_overrides_external(self):
        """Safety has higher priority than external."""
        comment = "# fail-open URL at https://example.com"
        assert classify(comment) == "keep_warning"

    def test_safety_overrides_why(self):
        """Safety has higher priority than WHY."""
        comment = "# fail-closed because it must never leak"
        assert classify(comment) == "keep_warning"

    def test_url_overrides_restate(self):
        """External has higher priority than restate."""
        comment = "# Filter results — see https://example.com"
        assert classify(comment) == "keep_external"

