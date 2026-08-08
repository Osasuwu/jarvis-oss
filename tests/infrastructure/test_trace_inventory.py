"""Tests for ``trace_inventory`` — comment candidate enumeration and pipeline.

AC coverage
-----------
AC1 — Enumerates comment candidates bucketed by category.
AC2 — Exclude-list correctness.
AC3 — Category-regex fixtures for each pattern.
AC4 — Output feeds directly into ``comment_classifier`` (single pipeline).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.comment_classifier import classify
from src.trace_inventory import (
    BANNER_LABEL,
    MARKETING,
    META_PROCESS,
    RESTATE,
    STANDARD,
    ClassifiedCandidate,
    inventory,
    run_pipeline,
    _categorize,
    _find_hash_comments,
    _find_py_comments,
    _find_ts_comments,
)


# =========================================================================
# Helpers
# =========================================================================


def _make_repo(files: dict[str, str]) -> Path:
    """Create a temporary repo from a dict of ``rel_path → content``.

    Returns the root ``Path``.
    """
    root = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


# =========================================================================
# AC3 — Category-regex fixtures for each pattern
# =========================================================================


class TestCategoryBannerLabel:
    """Comments that are section banners/separators → ``banner_label``."""

    def test_dashes(self):
        assert _categorize("# ----------") == BANNER_LABEL

    def test_equals(self):
        assert _categorize("# ==========") == BANNER_LABEL

    def test_stars(self):
        assert _categorize("# *********") == BANNER_LABEL

    def test_slashes(self):
        assert _categorize("# //////////") == BANNER_LABEL

    def test_mixed_separator(self):
        """Continuous separator characters form a banner."""
        assert _categorize("# -----=====*****-----") == BANNER_LABEL

    def test_banner_with_text_is_not_banner(self):
        """A line with meaningful text alongside separators is NOT a banner."""
        assert _categorize("# --- Section ---") != BANNER_LABEL
        assert _categorize("# == Config ==") != BANNER_LABEL

    def test_short_separator_not_banner(self):
        """Fewer than 5 repeated chars is not a banner."""
        assert _categorize("# ----") != BANNER_LABEL

    def test_sql_style_banner(self):
        assert _categorize("-- ----------") == BANNER_LABEL


class TestCategoryMarketing:
    """Comments with copyright, license, company → ``marketing``."""

    def test_copyright(self):
        assert _categorize("# Copyright 2024 Acme Corp") == MARKETING

    def test_all_rights_reserved(self):
        assert _categorize("# All rights reserved.") == MARKETING

    def test_license_statement(self):
        assert _categorize("# Licensed under the MIT License") == MARKETING

    def test_company_name(self):
        assert _categorize("# Acme Corp.") == MARKETING

    def test_author_tag(self):
        assert _categorize("# Author: John Doe") == MARKETING


class TestCategoryMetaProcess:
    """Comments with TODO, FIXME, HACK, etc. → ``meta_process``."""

    def test_todo(self):
        assert _categorize("# TODO: refactor this") == META_PROCESS

    def test_fixme(self):
        assert _categorize("# FIXME: this is broken") == META_PROCESS

    def test_hack(self):
        assert _categorize("# HACK: workaround for #123") == META_PROCESS

    def test_xxx(self):
        assert _categorize("# XXX: known limitation") == META_PROCESS

    def test_note(self):
        assert _categorize("# NOTE: this is important") == META_PROCESS

    def test_optimize(self):
        assert _categorize("# OPTIMIZE: slow path") == META_PROCESS

    def test_review(self):
        assert _categorize("# REVIEW: check this logic") == META_PROCESS

    def test_tbd(self):
        assert _categorize("# TBD: decide later") == META_PROCESS

    def test_revisit(self):
        assert _categorize("# REVISIT: after refactor") == META_PROCESS

    def test_workaround(self):
        assert _categorize("# WORKAROUND: upstream bug") == META_PROCESS

    def test_broken(self):
        assert _categorize("# BROKEN: does not work") == META_PROCESS

    def test_deprecated(self):
        assert _categorize("# DEPRECATED: use new API") == META_PROCESS

    def test_nolint(self):
        assert _categorize("# NOLINT: false positive") == META_PROCESS

    def test_todo_inline(self):
        """TODO in the middle of a comment."""
        assert _categorize("# This needs a TODO item") == META_PROCESS

    def test_noqa(self):
        assert _categorize("# noqa: E501") == META_PROCESS


class TestCategoryRestate:
    """Comments that start with a restate verb → ``restate``."""

    def test_filter_restate(self):
        assert _categorize("# Filter by project") == RESTATE

    def test_check_restate(self):
        assert _categorize("# Check if file exists") == RESTATE

    def test_validate_restate(self):
        assert _categorize("# Validate input") == RESTATE

    def test_import_restate(self):
        assert _categorize("# Import config") == RESTATE

    def test_convert_restate(self):
        assert _categorize("# Convert to int") == RESTATE

    def test_load_restate(self):
        assert _categorize("# Load data from disk") == RESTATE

    def test_save_restate(self):
        assert _categorize("# Save results") == RESTATE

    def test_third_person_verb(self):
        assert _categorize("# Gets the current user") == RESTATE
        assert _categorize("# Validates the input") == RESTATE

    def test_compute_restate(self):
        assert _categorize("# Compute the total") == RESTATE

    def test_extract_restate(self):
        assert _categorize("# Extract values") == RESTATE


class TestCategoryStandard:
    """Comments that don't match any special category → ``standard``."""

    def test_explanatory_comment(self):
        assert _categorize("# This was refactored for clarity") == STANDARD

    def test_question_comment(self):
        assert _categorize("# Is this still needed?") == STANDARD

    def test_context_comment(self):
        assert _categorize("# The caller already validates this") == STANDARD

    def test_ts_style_comment(self):
        """Category detection works on text regardless of comment prefix."""
        assert _categorize("// Some explanation") == STANDARD

    def test_sql_style_comment(self):
        assert _categorize("-- Some explanation") == STANDARD

    def test_empty_after_strip(self):
        """An empty comment yields standard."""
        assert _categorize("#") == STANDARD


class TestCategoryPriorityOrdering:
    """First-matching category wins."""

    def test_marketing_over_meta_process(self):
        """If both marketing and meta-process match, marketing wins."""
        assert _categorize("# Copyright 2024 — TODO: update") == MARKETING

    def test_meta_process_over_restate(self):
        """If both meta-process and restate match, meta-process wins."""
        assert _categorize("# TODO: Filter results") == META_PROCESS

    def test_banner_not_overridden(self):
        """Banner is checked first — a pure banner line is banner_label,
        not meta_process even if it resembles a TODO-like keyword.
        """
        assert _categorize("# ----------") == BANNER_LABEL
        assert _categorize("# --- TODO ---") == META_PROCESS


# =========================================================================
# AC2 — Exclude-list correctness
# =========================================================================


class TestExcludeDirs:
    """Directories in the exclude list are never enumerated."""

    def test_excludes_venv(self):
        root = _make_repo({
            ".venv/lib/code.py": "# This comment\n",
            "src/main.py": "# A real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_excludes_node_modules(self):
        root = _make_repo({
            "node_modules/pkg/index.js": "// node comment\n",
            "src/index.js": "// real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/index.js"

    def test_excludes_dist(self):
        root = _make_repo({
            "dist/bundle.js": "// dist comment\n",
            "src/lib.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/lib.py"

    def test_excludes_migrations(self):
        root = _make_repo({
            "migrations/001_init.sql": "-- migration comment\n",
            "src/schema.sql": "-- real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/schema.sql"

    def test_excludes_claude_dir(self):
        root = _make_repo({
            ".claude/settings.json": "# ignored\n",
            "src/main.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_excludes_claude_userlevel(self):
        root = _make_repo({
            ".claude-userlevel/skills/my-skill/SKILL.md": "# ignored\n",
            "src/main.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"


class TestExcludeFiles:
    """Specific files in the exclude list are skipped."""

    def test_excludes_telegram_mcp_server(self):
        root = _make_repo({
            "scripts/telegram-mcp-server.py": "# bot comment\n",
            "scripts/other.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "scripts/other.py"


class TestExcludeBasenames:
    """Governance files by basename are skipped."""

    def test_excludes_claude_md(self):
        root = _make_repo({
            "CLAUDE.md": "# governance\n",
            "src/CLAUDE.md": "# nested\n",
            "src/main.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_excludes_soul_md(self):
        root = _make_repo({
            "config/SOUL.md": "# identity\n",
            "src/main.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_excludes_context_md(self):
        root = _make_repo({
            "CONTEXT.md": "# domain\n",
            "src/main.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_excludes_binary_files(self):
        """Binary file extensions are not enumerated."""
        root = _make_repo({
            "src/main.py": "# real comment\n",
            "src/icon.png": "not really png but extension is what matters",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"


# =========================================================================
# AC1 — Comment enumeration across languages
# =========================================================================


class TestEnumerationPython:
    """Python comments via tokenize."""

    def test_single_line_comments(self):
        root = _make_repo({
            "main.py": (
                "# first line\n"
                "x = 1\n"
                "# second line\n"
            ),
        })
        result = inventory(root)
        assert len(result) == 2
        assert result[0].line_number == 1
        assert "# first line" in result[0].text
        assert result[1].line_number == 3
        assert "# second line" in result[1].text

    def test_inline_comment(self):
        root = _make_repo({
            "main.py": "x = 1  # inline comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].line_number == 1
        assert "inline" in result[0].text

    def test_multiline_comment_blocks(self):
        """Each comment line is a separate candidate."""
        root = _make_repo({
            "main.py": (
                "# line one\n"
                "# line two\n"
                "x = 1\n"
            ),
        })
        result = inventory(root)
        assert len(result) == 2

    def test_no_false_positives_in_strings(self):
        """A ``#`` inside a string is NOT a comment."""
        root = _make_repo({
            "main.py": (
                'url = "https://example.com"\n'
                "# actual comment\n"
            ),
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].line_number == 2

    def test_empty_file(self):
        root = _make_repo({"empty.py": ""})
        result = inventory(root)
        assert len(result) == 0

    def test_docstring_not_confused_as_comment(self):
        """Docstrings are STRING tokens, not COMMENT tokens."""
        root = _make_repo({
            "main.py": (
                '"""Module docstring."""\n'
                "# actual comment\n"
            ),
        })
        result = inventory(root)
        assert len(result) == 1
        assert "actual comment" in result[0].text


class TestEnumerationHashBased:
    """Hash-based comment languages (yml, sh, toml, etc.)."""

    @pytest.mark.parametrize("ext,name", [
        (".yml", "YAML"),
        (".yaml", "YAML"),
        (".sh", "shell"),
        (".bash", "bash"),
        (".ps1", "PowerShell"),
        (".toml", "TOML"),
    ])
    def test_hash_comments(self, ext, name):
        root = _make_repo({
            f"file{ext}": "# comment in " + name + "\ncode: value\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert f"comment in {name}" in result[0].text

    def test_inline_hash_comment(self):
        root = _make_repo({
            "config.yml": "key: value  # inline comment\n",
        })
        result = inventory(root)
        assert len(result) == 1

    def test_hash_in_string_not_comment(self):
        """A ``#`` inside a YAML string is not a comment."""
        root = _make_repo({
            "config.yml": 'url: "https://example.com"\n',
        })
        result = inventory(root)
        assert len(result) == 0

    def test_makefile_comments(self):
        root = _make_repo({
            "Makefile": "# comment in Makefile\nall:\n\techo hi\n",
        })
        result = inventory(root)
        assert len(result) == 1

    def test_dockerfile_comments(self):
        root = _make_repo({
            "Dockerfile": "# comment in Dockerfile\nFROM ubuntu:latest\n",
        })
        result = inventory(root)
        assert len(result) == 1


class TestEnumerationTypeScript:
    """TypeScript/JS comment styles."""

    @pytest.mark.parametrize("ext,name", [
        (".ts", "TypeScript"),
        (".tsx", "TSX"),
        (".js", "JavaScript"),
        (".jsx", "JSX"),
        (".mjs", "ES module"),
    ])
    def test_single_line_comment(self, ext, name):
        root = _make_repo({
            f"file{ext}": "// comment in " + name + "\nconst x = 1;\n",
        })
        result = inventory(root)
        assert len(result) == 1

    def test_block_comment(self):
        root = _make_repo({
            "main.ts": (
                "/*\n"
                " * Block comment\n"
                " */\n"
                "const x = 1;\n"
            ),
        })
        result = inventory(root)
        assert len(result) == 1
        assert "Block comment" in result[0].text

    def test_inline_comment_after_code(self):
        root = _make_repo({
            "main.ts": "const x = 1; // inline\n",
        })
        result = inventory(root)
        assert len(result) == 1

    def test_url_in_string_not_confused(self):
        root = _make_repo({
            "main.ts": 'const url = "https://example.com";\n',
        })
        result = inventory(root)
        assert len(result) == 0

    def test_regex_not_confused(self):
        root = _make_repo({
            "main.ts": "const re = /\\/\\/test/;\n// actual comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert "actual comment" in result[0].text


class TestEnumerationSQL:
    """SQL files with ``--`` comments."""

    def test_sql_comment(self):
        root = _make_repo({
            "schema.sql": "-- Create users table\nCREATE TABLE users (id INT);\n",
        })
        result = inventory(root)
        assert len(result) == 1


class TestEnumerationOrdering:
    """Results are ordered by file path then line number."""

    def test_sorted_output(self):
        root = _make_repo({
            "b/file.py": "# second file\n# another line\n",
            "a/file.py": "# first file\n",
        })
        result = inventory(root)
        assert len(result) == 3
        assert result[0].file_path == "a/file.py"
        assert result[1].file_path == "b/file.py"
        assert result[1].line_number == 1
        assert result[2].file_path == "b/file.py"
        assert result[2].line_number == 2


class TestCategoryAssignment:
    """Each comment candidate has a category."""

    def test_category_assigned(self):
        root = _make_repo({
            "main.py": (
                "# ----------\n"
                "# TODO: fix this\n"
                "# Filter results\n"
                "# Some explanation\n"
            ),
        })
        result = inventory(root)
        assert len(result) == 4
        categories = {c.line_number: c.category for c in result}
        assert categories[1] == BANNER_LABEL
        assert categories[2] == META_PROCESS
        assert categories[3] == RESTATE
        assert categories[4] == STANDARD

    def test_category_is_not_none(self):
        """Every candidate must have a non-None category."""
        root = _make_repo({
            "main.py": (
                "# Copyright 2024\n"
                "# --- === ***\n"
                "# TODO\n"
                "# Just a comment\n"
            ),
        })
        result = inventory(root)
        assert all(c.category is not None for c in result)
        assert all(c.category in (BANNER_LABEL, MARKETING, META_PROCESS, RESTATE, STANDARD)
                   for c in result)


# =========================================================================
# AC4 — Pipeline integration with comment_classifier
# =========================================================================


class TestPipelineIntegration:
    """``run_pipeline`` chains inventory → classify."""

    def test_pipeline_returns_classified_candidates(self):
        root = _make_repo({
            "main.py": (
                "# ----------\n"
                "# Filter results\n"
                "# fail-open path\n"
                "# Generic comment\n"
            ),
        })
        result = run_pipeline(root)
        assert len(result) == 4
        assert all(isinstance(c, ClassifiedCandidate) for c in result)

    def test_pipeline_preserves_fields(self):
        root = _make_repo({
            "main.py": "# Filter results\n",
        })
        result = run_pipeline(root)
        assert len(result) == 1
        c = result[0]
        assert c.file_path == "main.py"
        assert c.line_number == 1
        assert c.language == "py"
        assert c.category == RESTATE
        assert c.disposition == "remove"  # Pure restate → remove

    def test_pipeline_classifier_keep_warning(self):
        root = _make_repo({
            "main.py": "# fail-closed path\n",
        })
        result = run_pipeline(root)
        assert len(result) == 1
        assert result[0].disposition == "keep_warning"

    def test_pipeline_classifier_keep_external(self):
        root = _make_repo({
            "main.py": "# See https://example.com\n",
        })
        result = run_pipeline(root)
        assert len(result) == 1
        assert result[0].disposition == "keep_external"

    def test_pipeline_classifier_keep_unsure(self):
        root = _make_repo({
            "main.py": "# Some ambiguous comment\n",
        })
        result = run_pipeline(root)
        assert len(result) == 1
        assert result[0].disposition == "keep_unsure"

    def test_pipeline_obeys_excludes(self):
        root = _make_repo({
            ".venv/lib/code.py": "# filtered\n",
            "main.py": "# Filter results\n",
        })
        result = run_pipeline(root)
        assert len(result) == 1
        assert result[0].file_path == "main.py"

    def test_inventory_feeds_directly_into_classify(self):
        """The output of inventory() can be directly used with classify()."""
        root = _make_repo({
            "main.py": "# fail-open guardrail\n",
        })
        candidates = inventory(root)
        assert len(candidates) == 1
        disposition = classify(candidates[0].text)
        assert disposition == "keep_warning"


# =========================================================================
# Utility functions — _find_comments per language
# =========================================================================


class TestFindPyComments:
    def test_tokenize_based(self):
        content = "# comment\nx = 1\n"
        result = _find_py_comments(content)
        assert len(result) == 1
        assert result[0] == (1, "# comment")


class TestFindHashComments:
    def test_simple(self):
        result = _find_hash_comments("# comment\ncode\n# another\n", "#")
        assert len(result) == 2

    def test_full_line(self):
        result = _find_hash_comments("# full line\n", "#")
        assert len(result) == 1

    def test_inline(self):
        result = _find_hash_comments("code # inline\n", "#")
        assert len(result) == 1

    def test_not_in_string(self):
        result = _find_hash_comments('url = "https://example.com"\n', "#")
        assert len(result) == 0


class TestFindTSComments:
    def test_single_line(self):
        result = _find_ts_comments("// comment\nconst x = 1;\n")
        assert len(result) == 1

    def test_block_comment(self):
        result = _find_ts_comments("/*\n * block\n */\nconst x = 1;\n")
        assert len(result) == 1

    def test_inline(self):
        result = _find_ts_comments("const x = 1; // inline\n")
        assert len(result) == 1

    def test_not_in_string(self):
        result = _find_ts_comments('const url = "https://example.com";\n')
        assert len(result) == 0

    def test_not_in_regex(self):
        result = _find_ts_comments("const re = /\\/\\/test/;\n// real\n")
        assert len(result) == 1


# =========================================================================
# Edge cases
# =========================================================================


class TestEdgeCases:
    def test_nonexistent_directory(self):
        result = inventory("/tmp/nonexistent-dir-12345")
        assert len(result) == 0

    def test_non_code_files_skipped(self):
        root = _make_repo({
            "readme.md": "# Not a comment, it's markdown\n",
            "data.csv": "a,b,c\n1,2,3\n",
            "src/main.py": "# real comment\n",
        })
        result = inventory(root)
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_custom_exclude_dirs(self):
        root = _make_repo({
            "custom/lib/code.py": "# excluded\n",
            "src/main.py": "# included\n",
        })
        result = inventory(root, exclude_dirs=["custom"])
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"

    def test_custom_exclude_files(self):
        root = _make_repo({
            "src/secret.py": "# excluded\n",
            "src/main.py": "# included\n",
        })
        result = inventory(root, exclude_files={"src/secret.py"})
        assert len(result) == 1
        assert result[0].file_path == "src/main.py"


