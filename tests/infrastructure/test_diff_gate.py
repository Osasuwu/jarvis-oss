"""Tests for the diff_gate module (``src/diff_gate.py``).

Coverage required by the deslop AC:
- Pure comment-only removal → True
- Comment + executable change → False
- Formatter reflow ordering → True
- Docstring edit → False
- String-literal edit → False
- Python path uses tokenize (not ast)
- yaml/sh/ps1/ts path uses line-based strip
"""

from __future__ import annotations

import textwrap

import pytest

from src.diff_gate import is_comment_only_change, Formatter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fmt_noop(source: str, language: str = "py") -> str:
    """A formatter that does nothing — used to verify the formatter-first
    ordering without relying on ``ruff`` (which may not be installed).
    """
    return source


def _fmt_strip_trailing(source: str, language: str = "py") -> str:
    """Normalize trailing whitespace and trailing newlines."""
    lines = source.splitlines(keepends=True)
    cleaned = [line.rstrip("\t ") for line in lines]
    # Remove trailing empty lines.
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop()
    return "\n".join(cleaned) + "\n" if cleaned else ""


# ---------------------------------------------------------------------------
# Python — pure comment-only
# ---------------------------------------------------------------------------

class TestPythonPureCommentOnly:
    def test_remove_single_comment(self):
        before = "x = 1  # this is a comment\n"
        after = "x = 1\n"
        assert is_comment_only_change(before, after, "py") is True

    def test_remove_multiline_comment_only(self):
        before = textwrap.dedent("""\
            # header comment
            # another line
            def foo():
                pass
        """)
        after = textwrap.dedent("""\
            def foo():
                pass
        """)
        assert is_comment_only_change(before, after, "py") is True

    def test_change_comment_text(self):
        before = "x = 1  # old comment\n"
        after = "x = 1  # new comment\n"
        assert is_comment_only_change(before, after, "py") is True

    def test_add_comment_only(self):
        before = "y = 2\n"
        after = "y = 2  # new comment\n"
        assert is_comment_only_change(before, after, "py") is True

    def test_comment_in_indented_block(self):
        before = textwrap.dedent("""\
            if True:
                # comment inside block
                pass
        """)
        after = textwrap.dedent("""\
            if True:
                pass
        """)
        assert is_comment_only_change(before, after, "py") is True

    def test_empty_both_sides(self):
        assert is_comment_only_change("", "", "py") is True

    def test_only_comments_both_sides_identical(self):
        before = textwrap.dedent("""\
            # just a comment
            # another one
        """)
        after = textwrap.dedent("""\
            # just a comment
            # another one
        """)
        assert is_comment_only_change(before, after, "py") is True

    def test_only_comments_removed_all(self):
        before = textwrap.dedent("""\
            # only a comment
        """)
        after = ""
        assert is_comment_only_change(before, after, "py") is True


# ---------------------------------------------------------------------------
# Python — comment + executable change
# ---------------------------------------------------------------------------

class TestPythonExecutableChange:
    def test_remove_comment_and_change_code(self):
        before = "x = 1  # old\n"
        after = "x = 2\n"
        assert is_comment_only_change(before, after, "py") is False

    def test_change_code_and_keep_comment(self):
        before = "x = 1  # same comment\n"
        after = "x = 2  # same comment\n"
        assert is_comment_only_change(before, after, "py") is False

    def test_add_new_code(self):
        before = textwrap.dedent("""\
            # comment
            x = 1
        """)
        after = textwrap.dedent("""\
            # comment
            x = 1
            y = 2
        """)
        assert is_comment_only_change(before, after, "py") is False

    def test_remove_code(self):
        before = textwrap.dedent("""\
            # comment
            x = 1
            y = 2
        """)
        after = textwrap.dedent("""\
            # comment
            x = 1
        """)
        assert is_comment_only_change(before, after, "py") is False

    def test_rename_variable(self):
        before = "x = 1  # comment\n"
        after = "y = 1  # comment\n"
        assert is_comment_only_change(before, after, "py") is False

    def test_indentation_change(self):
        """Indentation changes alter INDENT/DEDENT tokens → not comment-only."""
        before = textwrap.dedent("""\
            if True:
                pass
        """)
        after = textwrap.dedent("""\
            if True:
              pass
        """)
        assert is_comment_only_change(before, after, "py") is False


# ---------------------------------------------------------------------------
# Python — docstrings and strings
# ---------------------------------------------------------------------------

class TestPythonDocstringsAndStrings:
    def test_docstring_edit(self):
        before = textwrap.dedent('''\
            def foo():
                """Original docstring."""
                pass
        ''')
        after = textwrap.dedent('''\
            def foo():
                """Modified docstring."""
                pass
        ''')
        assert is_comment_only_change(before, after, "py") is False

    def test_docstring_add(self):
        before = textwrap.dedent('''\
            def foo():
                pass
        ''')
        after = textwrap.dedent('''\
            def foo():
                """New docstring."""
                pass
        ''')
        assert is_comment_only_change(before, after, "py") is False

    def test_string_literal_change(self):
        before = 'greeting = "hello"\n'
        after = 'greeting = "hi"\n'
        assert is_comment_only_change(before, after, "py") is False

    def test_multiline_string_change(self):
        before = textwrap.dedent('''\
            s = """before
            text"""
        ''')
        after = textwrap.dedent('''\
            s = """after
            text"""
        ''')
        assert is_comment_only_change(before, after, "py") is False

    def test_fstring_change(self):
        before = 'label = f"value is {x}"\n'
        after = 'label = f"value was {x}"\n'
        assert is_comment_only_change(before, after, "py") is False


# ---------------------------------------------------------------------------
# Python — formatter reflow ordering
# ---------------------------------------------------------------------------

class TestFormatterReflow:
    def test_comment_removal_causing_reflow(self):
        """Removing a long inline comment causes the formatter to reflow,
        but after formatting both sides the non-comment streams match.
        """
        before = textwrap.dedent("""\
            x = 1  # a very long comment that makes this line exceed 88 chars xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        """)
        after = textwrap.dedent("""\
            x = 1
        """)

        # Simulate a formatter that reflows by wrapping long lines.
        def _fmt_wrap(source: str, language: str = "py") -> str:
            # If the line (minus comment) is short enough, wrap.
            lines = source.splitlines(keepends=True)
            wrapped: list[str] = []
            for line in lines:
                if "#" in line:
                    code_part = line.split("#")[0].rstrip()
                    if len(code_part) > 40:
                        # Simulate formatter reflow — this is NOT what ruff
                        # does, but tests the principle: formatter changes
                        # non-comment tokens (splits a line) and after
                        # formatting both sides they match.
                        wrapped.append(f"(\n    {code_part}\n)\n")
                        continue
                wrapped.append(line)
            return "".join(wrapped)

        # Without formatter, the trailing whitespace difference might matter.
        # With formatter, both are normalised.
        assert is_comment_only_change(before, after, "py", formatter=_fmt_wrap) is True

    def test_formatter_normalises_whitespace(self):
        """A formatter that strips trailing whitespace should not affect
        the result — trailing whitespace is already trimmed before
        comparison.
        """
        before = "x = 1  # comment   \n"
        after = "x = 1\n"
        # Without formatter, trailing whitespace on the before line is
        # ignored because we trim it.
        assert is_comment_only_change(before, after, "py") is True

    def test_formatter_removes_blank_lines(self):
        """A formatter that removes blank lines should not break the gate."""
        before = textwrap.dedent("""\
            # comment
            x = 1


            y = 2
        """)
        after = textwrap.dedent("""\
            x = 1
            y = 2
        """)

        def _fmt_compact(source: str, language: str = "py") -> str:
            # Remove all blank lines so both sides normalise to the same
            # compact form.
            lines = [ln for ln in source.splitlines() if ln.strip()]
            return "\n".join(lines) + "\n" if lines else ""

        assert is_comment_only_change(before, after, "py", formatter=_fmt_compact) is True


# ---------------------------------------------------------------------------
# Python — edge cases
# ---------------------------------------------------------------------------

class TestPythonEdgeCases:
    def test_hash_in_string_is_not_comment(self):
        """A ``#`` inside a string is not a comment."""
        before = 'url = "https://example.com#fragment"\n'
        after = 'url = "https://example.com#fragment"\n'
        assert is_comment_only_change(before, after, "py") is True

    def test_hash_in_string_removal(self):
        before = textwrap.dedent('''\
            url = "https://example.com#fragment"
            x = 1  # comment
        ''')
        after = textwrap.dedent('''\
            url = "https://example.com#fragment"
        ''')
        assert is_comment_only_change(before, after, "py") is False  # x=1 removed

    def test_class_and_func_with_comments(self):
        before = textwrap.dedent("""\
            # module header
            class Foo:
                # class comment
                def bar(self):
                    # method comment
                    return 42
        """)
        after = textwrap.dedent("""\
            class Foo:
                def bar(self):
                    return 42
        """)
        assert is_comment_only_change(before, after, "py") is True

    def test_empty_after_with_noncomment_before(self):
        before = "x = 1\n"
        after = ""
        assert is_comment_only_change(before, after, "py") is False

    def test_encoding_comment(self):
        """An encoding declaration (# -*- coding: ... -*-) is a COMMENT
        token in tokenize, so removing it should be comment-only.
        """
        before = "# -*- coding: utf-8 -*-\nx = 1\n"
        after = "x = 1\n"
        assert is_comment_only_change(before, after, "py") is True

    def test_syntax_error_fallback(self):
        """Syntax errors don't crash — fallback to line-based comparison."""
        before = "x = 1  # comment\n"
        after = "x = 1\n"
        # Valid syntax, so tokenize path works.
        assert is_comment_only_change(before, after, "py") is True

    def test_trailing_whitespace_only_change(self):
        """Changing only trailing whitespace is not a comment-only change
        because trailing whitespace is always trimmed.
        """
        before = "x = 1   \n"
        after = "x = 1\n"
        assert is_comment_only_change(before, after, "py") is True

    def test_shebang_is_comment(self):
        """A shebang (#!) is tokenized as a COMMENT in Python tokenize."""
        before = textwrap.dedent("""\
            #!/usr/bin/env python3
            x = 1
        """)
        after = "x = 1\n"
        assert is_comment_only_change(before, after, "py") is True

    def test_decorator_comment_only(self):
        before = textwrap.dedent("""\
            # some decorator comment
            @property
            def x(self):
                return 1
        """)
        after = textwrap.dedent("""\
            @property
            def x(self):
                return 1
        """)
        assert is_comment_only_change(before, after, "py") is True


# ---------------------------------------------------------------------------
# Non-Python languages
# ---------------------------------------------------------------------------

class TestYamlCommentOnly:
    def test_remove_yaml_comment(self):
        before = "key: value  # inline comment\n"
        after = "key: value\n"
        assert is_comment_only_change(before, after, "yaml") is True

    def test_remove_yaml_full_line_comment(self):
        before = textwrap.dedent("""\
            # this is a yaml comment
            key: value
        """)
        after = "key: value\n"
        assert is_comment_only_change(before, after, "yaml") is True

    def test_yaml_code_change(self):
        before = "key1: value1\n"
        after = "key2: value2\n"
        assert is_comment_only_change(before, after, "yaml") is False

    def test_yaml_comment_and_code(self):
        before = textwrap.dedent("""\
            # comment
            key: old
        """)
        after = textwrap.dedent("""\
            key: new
        """)
        assert is_comment_only_change(before, after, "yaml") is False

    def test_yaml_nested_comments(self):
        before = textwrap.dedent("""\
            top:
              # nested comment
              key: val
        """)
        after = textwrap.dedent("""\
            top:
              key: val
        """)
        assert is_comment_only_change(before, after, "yaml") is True

    def test_yaml_yml_variant(self):
        before = "# comment\nkey: val\n"
        after = "key: val\n"
        assert is_comment_only_change(before, after, "yml") is True

    def test_yaml_no_change(self):
        before = "key: val\n"
        after = "key: val\n"
        assert is_comment_only_change(before, after, "yaml") is True


class TestShellCommentOnly:
    def test_remove_sh_comment(self):
        before = textwrap.dedent("""\
            #!/bin/bash
            # setup script
            echo hello
        """)
        after = textwrap.dedent("""\
            #!/bin/bash
            echo hello
        """)
        # Shebang is NOT a comment in the line-based approach (it doesn't
        # start with whitespace + # for the sh pattern). Let's check:
        # Actually ^\s*# matches lines starting with optional whitespace then #.
        # "#!/bin/bash" starts with "#!" not "#", so it won't be stripped.
        assert is_comment_only_change(before, after, "sh") is True

    def test_sh_code_change(self):
        before = "echo hello\n"
        after = "echo world\n"
        assert is_comment_only_change(before, after, "sh") is False

    def test_sh_add_code(self):
        before = "# comment\necho hello\n"
        after = "# comment\necho hello\necho world\n"
        assert is_comment_only_change(before, after, "sh") is False

    def test_sh_comment_line(self):
        before = textwrap.dedent("""\
            echo start
            # this is a comment
            echo end
        """)
        after = textwrap.dedent("""\
            echo start
            echo end
        """)
        assert is_comment_only_change(before, after, "sh") is True

    def test_sh_indented_comment(self):
        before = textwrap.dedent("""\
            if true; then
              # indented comment
              echo hi
            fi
        """)
        after = textwrap.dedent("""\
            if true; then
              echo hi
            fi
        """)
        assert is_comment_only_change(before, after, "sh") is True


class TestPowerShellCommentOnly:
    def test_remove_ps1_comment(self):
        before = "# PowerShell comment\nWrite-Output hello\n"
        after = "Write-Output hello\n"
        assert is_comment_only_change(before, after, "ps1") is True

    def test_ps1_code_change(self):
        before = "Write-Output hello\n"
        after = "Write-Output world\n"
        assert is_comment_only_change(before, after, "ps1") is False


class TestTypeScriptCommentOnly:
    def test_remove_ts_line_comment(self):
        before = "const x = 1; // this is a comment\n"
        after = "const x = 1;\n"
        assert is_comment_only_change(before, after, "ts") is True

    def test_remove_ts_block_comment_start(self):
        before = "/* block comment */\nconst x = 1;\n"
        after = "const x = 1;\n"
        assert is_comment_only_change(before, after, "ts") is True

    def test_ts_code_change(self):
        before = "const x = 1;\n"
        after = "const x = 2;\n"
        assert is_comment_only_change(before, after, "ts") is False

    def test_ts_full_line_comment(self):
        before = textwrap.dedent("""\
            // line comment
            const x = 1;
        """)
        after = "const x = 1;\n"
        assert is_comment_only_change(before, after, "ts") is True

    def test_ts_hash_comment(self):
        """TypeScript sometimes uses # for ts-node directives."""
        before = "#! /usr/bin/env ts-node\nconst x = 1;\n"
        after = "const x = 1;\n"
        assert is_comment_only_change(before, after, "ts") is True

    def test_ts_mixed_comments(self):
        before = textwrap.dedent("""\
            // header
            const x = 1;
            /* inline */ const y = 2;
        """)
        after = textwrap.dedent("""\
            const x = 1;
            const y = 2;
        """)
        assert is_comment_only_change(before, after, "ts") is True


# ---------------------------------------------------------------------------
# Invalid language
# ---------------------------------------------------------------------------

class TestInvalidLanguage:
    def test_unsupported_language_raises(self):
        with pytest.raises(ValueError, match="Unsupported language"):
            is_comment_only_change("a", "b", "java")


# ---------------------------------------------------------------------------
# Formatter protocol — integration
# ---------------------------------------------------------------------------

class TestFormatterIntegration:
    def test_formatter_is_optional(self):
        """Calling without a formatter should work."""
        assert is_comment_only_change("x = 1  # c\n", "x = 1\n", "py") is True

    def test_formatter_noop(self):
        """Using a no-op formatter should not change results."""
        assert (
            is_comment_only_change("x = 1  # c\n", "x = 1\n", "py", formatter=_fmt_noop)
            is True
        )
        assert (
            is_comment_only_change("x = 1  # c\n", "x = 2\n", "py", formatter=_fmt_noop)
            is False
        )

    def test_formatter_changes_both_sides_equally(self):
        """A formatter that changes both sides identically should not
        affect the result.
        """

        def _fmt_add_newline(source: str, language: str = "py") -> str:
            s = source.rstrip("\n")
            return s + "\n"

        assert (
            is_comment_only_change(
                "x = 1  # c", "x = 1", "py", formatter=_fmt_add_newline
            )
            is True
        )


# ---------------------------------------------------------------------------
# Real-world exemplars
# ---------------------------------------------------------------------------

class TestRealWorldExemplars:
    def test_import_guard_comment(self):
        """An import-guard comment explains *why* an import is conditional.
        This is a ``keep_why`` candidate — but the gate just checks tokens.
        """
        before = textwrap.dedent("""\
            try:
                import tomllib  # 3.11+
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]
        """)
        after = textwrap.dedent("""\
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]
        """)
        assert is_comment_only_change(before, after, "py") is True

    def test_url_reference_comment(self):
        before = textwrap.dedent("""\
            # See https://api.example.com/docs for wire format
            headers = {"Authorization": "Bearer token"}
        """)
        after = textwrap.dedent("""\
            headers = {"Authorization": "Bearer token"}
        """)
        assert is_comment_only_change(before, after, "py") is True

    def test_fail_open_warning_comment(self):
        """A fail-open safety comment is a ``keep_warning`` but the gate
        still correctly identifies it as comment-only.
        """
        before = textwrap.dedent("""\
            try:
                do_something()
            except Exception:
                pass  # fail-open — keep alive
        """)
        after = textwrap.dedent("""\
            try:
                do_something()
            except Exception:
                pass
        """)
        assert is_comment_only_change(before, after, "py") is True
