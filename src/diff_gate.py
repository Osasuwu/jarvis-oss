"""diff_gate — mechanically prove a changeset is comment-only.

``is_comment_only_change(before, after, language)`` returns ``True`` when
the only difference between two source strings is in comments.

Python path uses ``tokenize`` (stdlib) — drop COMMENT tokens, assert the
remaining token stream is byte-identical.
Non-Python paths (yaml, sh, ps1, ts) fall back to a line-based comment-strip
textual diff.

Format ordering: trim → run repo formatter if available → compare.
"""

from __future__ import annotations

import io
import re
import tokenize
from typing import Protocol


class Formatter(Protocol):
    """A callable that formats source code."""

    def __call__(self, source: str, language: str) -> str: ...


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_comment_only_change(
    before: str,
    after: str,
    language: str = "py",
    *,
    formatter: Formatter | None = None,
) -> bool:
    """Return ``True`` if the change between *before* and *after* is only in
    comments.

    Parameters
    ----------
    before:
        Original source text.
    after:
        Modified source text.
    language:
        One of ``"py"``, ``"yml"``, ``"yaml"``, ``"sh"``, ``"ps1"``, ``"ts"``.
    formatter:
        Optional formatter callable. If provided, both *before* and *after*
        are formatted before comparison so that comment-only changes that
        trigger a formatter reflow don't register as code changes.

    Returns
    -------
    ``True`` if the only differences are in comments, ``False`` otherwise.
    """
    _validate_language(language)

    # Trim trailing whitespace from every line.
    before = _trim_trailing_whitespace(before)
    after = _trim_trailing_whitespace(after)

    # Optionally normalise both sides with the repo formatter.
    if formatter is not None:
        before = formatter(before, language)
        after = formatter(after, language)

    if language == "py":
        return _py_comment_only(before, after)
    else:
        return _line_based_comment_only(before, after, language)


# ---------------------------------------------------------------------------
# Language support
# ---------------------------------------------------------------------------

_LANGUAGES = frozenset({"py", "yml", "yaml", "sh", "ps1", "ts"})


def _validate_language(language: str) -> None:
    if language not in _LANGUAGES:
        raise ValueError(
            f"Unsupported language {language!r}. "
            f"Supported: {', '.join(sorted(_LANGUAGES))}"
        )


# ---------------------------------------------------------------------------
# Python path — tokenize
# ---------------------------------------------------------------------------

# Token types we ignore during comparison — they carry no executable meaning.
_SKIP_TYPES: frozenset[int] = frozenset({
    tokenize.COMMENT,
    tokenize.ENDMARKER,
    tokenize.ENCODING,
})


def _py_comment_only(before: str, after: str) -> bool:
    """Compare two Python sources by token stream, ignoring comments.

    Uses ``tokenize.generate_tokens`` which is more robust than ``ast``:
    it preserves formatting, doesn't normalise strings, and doesn't swallow
    comments as docstrings.
    """
    before_tokens = _extract_tokens(before)
    after_tokens = _extract_tokens(after)

    if len(before_tokens) != len(after_tokens):
        return False

    return all(
        bt.type == at.type and bt.string == at.string
        for bt, at in zip(before_tokens, after_tokens)
    )


def _extract_tokens(source: str) -> list[tokenize.TokenInfo]:
    """Tokenise *source* and return tokens with ``COMMENT``/``ENDMARKER``/
    ``ENCODING`` removed.

    When a ``COMMENT`` token is removed, the ``NL`` token immediately
    following it (the comment line's terminator) is also removed — otherwise
    deleting a full-line comment always produces a token-count mismatch.
    """
    result: list[tokenize.TokenInfo] = []
    skip_nl = False  # set True after a COMMENT on its own line
    try:
        for tok in tokenize.generate_tokens(
            io.StringIO(source).readline
        ):
            if tok.type == tokenize.COMMENT:
                skip_nl = True
                continue
            if skip_nl and tok.type == tokenize.NL:
                skip_nl = False
                continue
            skip_nl = False
            if tok.type not in _SKIP_TYPES:
                result.append(tok)
    except tokenize.TokenError:
        # Source has a syntax error — fall back to line-based comparison.
        return _line_based_comment_only_result(source)
    return result


def _line_based_comment_only_result(source: str) -> list[_FakeToken]:
    """Fallback: return non-comment lines as fake tokens so the caller can
    still compare byte-identically when tokenize fails (e.g. incomplete
    multi-line string in the middle of an edit).
    """
    lines = source.splitlines(keepends=True)
    result: list[_FakeToken] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        result.append(_FakeToken(string=line))
    return result


class _FakeToken:
    """Minimal stand-in for ``tokenize.TokenInfo`` when tokenize fails."""
    __slots__ = ("type", "string")

    def __init__(self, string: str, typ: int = 0):
        self.type = typ
        self.string = string


# ---------------------------------------------------------------------------
# Non-Python path — line-based comment strip (inline + full-line)
# ---------------------------------------------------------------------------


def _line_based_comment_only(before: str, after: str, language: str) -> bool:
    """Compare two sources line-by-line, stripping comments.

    Shell/PowerShell/YAML/TypeScript don't have a robust tokeniser available
    in stdlib, so we use a line-based heuristic:
    - Strip inline comment suffixes (e.g. everything after ``#`` for YAML/shell).
    - Drop lines that are comment-only.
    - Assert remaining text is identical.
    """
    strip_fn = _INLINE_STRIPPERS[language]
    before_clean = _strip_inline_comments(before, strip_fn)
    after_clean = _strip_inline_comments(after, strip_fn)
    return before_clean == after_clean


# Each entry is a function that strips the comment part from a single line.
_INLINE_STRIPPERS: dict[str, str] = {
    "yml": "hash",
    "yaml": "hash",
    "sh": "hash",
    "ps1": "hash",
    "ts": "ts",
}


def _strip_inline_comments(source: str, mode: str) -> str:
    """Strip comments from *source*, returning only non-comment text.

    *mode* determines how comments are identified:
    - ``"hash"`` — strip everything from first ``#`` to end-of-line.
    - ``"ts"``  — strip ``//`` to end-of-line, handle ``/* */``, strip
      shebang/hash-prefixed lines.
    """
    lines: list[str] = []
    in_block_comment = False
    for line in source.splitlines(keepends=False):
        stripped = line.strip()

        # Track TS block-comment state across lines.
        if mode == "ts":
            if in_block_comment:
                idx = line.find("*/")
                if idx != -1:
                    line = line[idx + 2:].lstrip()
                    in_block_comment = False
                else:
                    continue  # still inside the block comment
            if "/*" in line and "*/" not in line:
                # Block comment opens and does not close on this line.
                line = line[: line.find("/*")]
                in_block_comment = True
            elif "/*" in line:
                # Block comment opens and closes on the same line.
                before_comment = line[: line.find("/*")]
                after_comment = line[line.find("*/") + 2:]
                line = before_comment + after_comment.lstrip()
            elif "//" in line:
                # Single-line comment.
                line = line[: line.find("//")]

        # Strip inline comment suffix (hash for yml/sh/ps1).
        if mode == "hash":
            idx = _find_hash_comment(line)
            if idx is not None:
                line = line[:idx]

        # Remove trailing whitespace left by inline comment removal.
        line = line.rstrip()

        # Skip full-line comment-only lines (shebang, etc.).
        if mode == "ts" and (stripped.startswith("#") or stripped.startswith("//")):
            continue

        if line:
            lines.append(line)

    return "\n".join(lines)

    return "\n".join(lines)


def _find_hash_comment(line: str) -> int | None:
    """Find the position of the first ``#`` that starts a comment in *line*.

    This is a heuristic: ``#`` inside a quoted string is not a comment
    start.  Only handles the common cases; unlikely to be perfect for all
    edge cases, but ``diff_gate`` explicitly says "no parser available" for
    non-Python languages.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return i
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trim_trailing_whitespace(source: str) -> str:
    """Strip trailing whitespace from every line, preserving line endings."""
    lines = source.splitlines(keepends=True)
    cleaned = (line.rstrip("\t ") for line in lines)
    return "".join(cleaned)
