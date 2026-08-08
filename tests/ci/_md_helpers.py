"""Shared markdown-parsing helpers for tests/ci guard tests."""

from __future__ import annotations

import re

_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_SPAN_RE = re.compile(r"`[^`\n]*`")

# A line whose *entire* content is `@<path>`. This is the only import form with
# positive evidence of resolving in this repo: project `CLAUDE.md`'s
# `@docs/context/invariants.md` expands into every session, while
# `.claude-userlevel/CLAUDE.md`'s two mid-prose imports did not — even though
# both of their targets existed on disk at the right path (#1426).
_BARE_IMPORT_RE = re.compile(r"^@(\S+)[ \t]*$")
_FENCE_LINE_RE = re.compile(r"^\s*(```|~~~)")


def _blank_out(match: re.Match[str]) -> str:
    """Replace a match with spaces, preserving newlines so line numbers hold."""
    return re.sub(r"[^\n]", " ", match.group(0))


def strip_code_spans_and_fences(text: str) -> str:
    """Drop fenced code blocks and inline `code spans` — import-line parsing
    skips code spans, so a match only counts if it survives outside one."""
    text = _FENCE_BLOCK_RE.sub("", text)
    text = _SPAN_RE.sub("", text)
    return text


def mask_code_spans_and_fences(text: str) -> str:
    """Like `strip_code_spans_and_fences`, but blanks rather than deletes, so
    line numbers and line-start anchoring survive for `^`-anchored matching."""
    text = _FENCE_BLOCK_RE.sub(_blank_out, text)
    text = _SPAN_RE.sub(_blank_out, text)
    return text


def find_bare_imports(text: str) -> list[tuple[int, str]]:
    """Return `(lineno, path)` for every bare, line-start `@path` import.

    This checks the *form*, not merely the presence of the substring. An
    `@Foo.md` mention embedded in a prose sentence is not returned: there is no
    evidence such a mention loads the file, and #1426 is the case where two of
    them silently delivered nothing for months while a substring-matching guard
    stayed green.

    Canonical implementation — `test_push_surface_guard.py` and the import-form
    guards share it so the definition of "an import that actually costs bytes"
    cannot drift from the definition of "an import that actually loads".
    """
    found: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _BARE_IMPORT_RE.match(line)
        if match:
            found.append((lineno, match.group(1)))
    return found
