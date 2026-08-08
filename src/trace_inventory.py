"""trace_inventory — enumerate comment candidates across a repo.

Interface::

    inventory(repo_root, ...) -> list[CommentCandidate]
    run_pipeline(repo_root, ...) -> list[ClassifiedCandidate]

Each candidate carries its file path, line number, comment text, language,
and a pre-classification category used for bucketing before the classifier
runs.  ``run_pipeline`` chains ``inventory`` → ``comment_classifier.classify``
into a single pipeline.
"""

from __future__ import annotations

import io
import os
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.comment_classifier import classify

__all__ = [
    "CommentCandidate",
    "ClassifiedCandidate",
    "inventory",
    "run_pipeline",
    "BANNER_LABEL",
    "RESTATE",
    "MARKETING",
    "META_PROCESS",
    "STANDARD",
]

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

BANNER_LABEL = "banner_label"
RESTATE = "restate"
MARKETING = "marketing"
META_PROCESS = "meta_process"
STANDARD = "standard"

# ---------------------------------------------------------------------------
# Default exclude lists
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".venv",
    "node_modules",
    "dist",
    "migrations",
    ".claude",
    ".claude-userlevel",
})

DEFAULT_EXCLUDE_FILES: frozenset[str] = frozenset({
    "scripts/telegram-mcp-server.py",
})

DEFAULT_EXCLUDE_BASENAMES: frozenset[str] = frozenset({
    "CLAUDE.md",
    "SOUL.md",
    "CONTEXT.md",
})

# ---------------------------------------------------------------------------
# Regex patterns per category
# ---------------------------------------------------------------------------

_BANNER_RE = re.compile(r"^[#!\s]*[-=*\/]{5,}\s*$")

_MARKETING_RE = re.compile(
    r"(?:copyright|all\s+rights\s+reserved|license|licensed\s+under|"
    r"author:|company:|\binc\.?\s*$|\bcorp\.?\s*$|\bltd\.?\s*$)",
    re.IGNORECASE,
)

_META_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bHACK\b"),
    re.compile(r"\bXXX\b"),
    re.compile(r"\bNOTE\b"),
    re.compile(r"\bOPTIMIZE\b"),
    re.compile(r"\bREVIEW\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bREVISIT\b"),
    re.compile(r"\bWORKAROUND\b"),
    re.compile(r"\bBROKEN\b"),
    re.compile(r"\bDEPRECATED\b"),
    re.compile(r"\bNOLINT\b"),
    re.compile(r"\bnoqa\b"),
]

_RESTATE_RE = re.compile(
    r"^(?:filter|check|get|set|create|update|delete|find|load|save|"
    r"parse|format|validate|convert|build|run|call|initialize|configure|"
    r"register|import|export|iterate|loop|increment|decrement|count|sum|"
    r"compute|calculate|extract|transform|map|reduce)s?\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Language support
# ---------------------------------------------------------------------------

_EXTENSION_LANG: dict[str, str] = {
    ".py": "py",
    ".pyi": "py",
    ".yml": "yml",
    ".yaml": "yaml",
    ".sh": "sh",
    ".bash": "sh",
    ".zsh": "sh",
    ".ps1": "ps1",
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "ts",
    ".jsx": "ts",
    ".mjs": "ts",
    ".cjs": "ts",
    ".sql": "sql",
    ".toml": "hash",
    ".cfg": "hash",
    ".ini": "hash",
}

_BINARY_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo",
    ".so", ".dll", ".dylib",
    ".zip", ".tar", ".gz", ".bz2",
    ".db", ".sqlite",
})

# Comment-char per language for inline finding
_COMMENT_CHAR: dict[str, str] = {
    "py": "#",
    "yml": "#",
    "yaml": "#",
    "sh": "#",
    "ps1": "#",
    "sql": "--",
    "hash": "#",
    "ts": "//",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CommentCandidate:
    """A single comment candidate found by ``inventory``.

    Attributes
    ----------
    file_path:
        Path relative to repo root.
    line_number:
        1-based line number where the comment starts.
    text:
        The full comment text including the comment prefix (``#``, ``//``, …).
    language:
        Language identifier (``py``, ``yml``, ``sh``, …).
    category:
        Pre-classification category.
    """

    file_path: str
    line_number: int
    text: str
    language: str
    category: str = STANDARD


@dataclass
class ClassifiedCandidate:
    """A comment candidate after running through the classifier pipeline.

    Attributes
    ----------
    file_path:
        Path relative to repo root.
    line_number:
        1-based line number.
    text:
        The full comment text.
    language:
        Language identifier.
    category:
        Pre-classification category.
    disposition:
        Classifier disposition (``remove``, ``keep_why``, …).
    """

    file_path: str
    line_number: int
    text: str
    language: str
    category: str
    disposition: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inventory(
    repo_root: str | Path,
    *,
    exclude_dirs: Sequence[str] | None = None,
    exclude_files: Sequence[str] | None = None,
    exclude_basenames: Sequence[str] | None = None,
) -> list[CommentCandidate]:
    """Enumerate comment candidates across *repo_root*.

    Walks the directory tree, finds comments in every eligible file, and
    assigns a pre-classification category to each.  Skips excluded dirs,
    files, and basenames, plus binary files.

    Parameters
    ----------
    repo_root:
        Root directory of the repository.
    exclude_dirs:
        Directory names to skip (matched at any depth).
        Defaults to ``DEFAULT_EXCLUDE_DIRS``.
    exclude_files:
        Relative file paths to skip.
        Defaults to ``DEFAULT_EXCLUDE_FILES``.
    exclude_basenames:
        Filenames to skip regardless of path.
        Defaults to ``DEFAULT_EXCLUDE_BASENAMES``.

    Returns
    -------
    List of ``CommentCandidate``, ordered by file path then line number.
    """
    root = Path(repo_root).resolve()
    skip_dirs = set(exclude_dirs) if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
    skip_files = set(exclude_files) if exclude_files is not None else DEFAULT_EXCLUDE_FILES
    skip_basenames = (
        set(exclude_basenames) if exclude_basenames is not None else DEFAULT_EXCLUDE_BASENAMES
    )

    candidates: list[CommentCandidate] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Prune excluded directories in-place (os.walk respects this).
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        rel_dir = os.path.relpath(dirpath, root)
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext in _BINARY_EXTS:
                continue
            if fn in skip_basenames:
                continue

            rel_path = os.path.join(rel_dir, fn) if rel_dir != "." else fn
            if rel_path in skip_files:
                continue

            lang = _detect_language(fn)
            if lang is None:
                continue

            comments = _find_comments(Path(dirpath) / fn, lang)
            for line_no, text in comments:
                category = _categorize(text)
                candidates.append(
                    CommentCandidate(
                        file_path=rel_path,
                        line_number=line_no,
                        text=text,
                        language=lang,
                        category=category,
                    )
                )

    candidates.sort(key=lambda c: (c.file_path, c.line_number))
    return candidates


def run_pipeline(
    repo_root: str | Path,
    *,
    exclude_dirs: Sequence[str] | None = None,
    exclude_files: Sequence[str] | None = None,
    exclude_basenames: Sequence[str] | None = None,
) -> list[ClassifiedCandidate]:
    """Run the full deslop pipeline: inventory → classify.

    Enumerates all comment candidates via ``inventory``, then runs each
    through ``comment_classifier.classify``.

    Returns
    -------
    List of ``ClassifiedCandidate``, ordered by file path then line number.
    """
    candidates = inventory(
        repo_root,
        exclude_dirs=exclude_dirs,
        exclude_files=exclude_files,
        exclude_basenames=exclude_basenames,
    )
    return [
        ClassifiedCandidate(
            file_path=c.file_path,
            line_number=c.line_number,
            text=c.text,
            language=c.language,
            category=c.category,
            disposition=classify(c.text),
        )
        for c in candidates
    ]


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def _detect_language(filename: str) -> str | None:
    """Detect language from *filename*.

    Returns a language key or ``None`` for non-code files.
    """
    # Special filenames without a standard extension.
    base = os.path.basename(filename)
    if base in ("Makefile", "makefile", "GNUmakefile"):
        return "hash"
    if base == "Dockerfile":
        return "hash"

    ext = os.path.splitext(filename)[1].lower()
    return _EXTENSION_LANG.get(ext)


# ---------------------------------------------------------------------------
# Comment finding per language
# ---------------------------------------------------------------------------


def _find_comments(filepath: Path, language: str) -> list[tuple[int, str]]:
    """Find all comments in *filepath* for the given *language*.

    Returns a list of ``(line_number, comment_text)`` tuples.
    Line numbers are 1-based.
    """
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    if language == "py":
        return _find_py_comments(source)
    elif language == "ts":
        return _find_ts_comments(source)
    elif language == "sql":
        return _find_hash_comments(source, "--")
    else:
        # hash-based: yml, yaml, sh, ps1, hash
        return _find_hash_comments(source, "#")


def _find_py_comments(source: str) -> list[tuple[int, str]]:
    """Find Python comments using ``tokenize``."""
    comments: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                text = tok.line.rstrip("\n")
                comments.append((tok.start[0], text))
    except tokenize.TokenError:
        # Fall back to line-based for incomplete sources.
        return _find_hash_comments(source, "#")
    return comments


def _find_ts_comments(source: str) -> list[tuple[int, str]]:
    """Find TypeScript/JS comments (``//`` and ``/* */``)."""
    comments: list[tuple[int, str]] = []
    lines = source.splitlines(keepends=True)
    in_block = False
    block_start = 0
    block_lines: list[str] = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        if in_block:
            block_lines.append(line.rstrip("\n"))
            if "*/" in stripped:
                in_block = False
                text = "\n".join(block_lines)
                comments.append((block_start, text))
                block_lines = []
                # Check for code after */ on the same line.
                after = stripped[stripped.index("*/") + 2:].strip()
                if after.startswith("//"):
                    comments.append((i, line.rstrip("\n")))
            continue

        # Check for /* */ (block comment)
        if "/*" in stripped:
            block_start = i
            block_lines = [line.rstrip("\n")]
            if "*/" in stripped:
                # Single-line block comment
                block_text = line.rstrip("\n")
                block_lines = []
                in_block = False
                comments.append((i, block_text))
                # Check for trailing // after */
                after = stripped[stripped.index("*/") + 2:].strip()
                if after.startswith("//"):
                    comments.append((i, line.rstrip("\n")))
            else:
                in_block = True
                continue

        # Check for // comment
        # Handle // inside strings, regex, etc. — simple heuristic avoids
        # // inside quoted strings.
        idx = _find_ts_inline_comment(line)
        if idx is not None:
            comments.append((i, line.rstrip("\n")))

    # If block comment never closed, emit what we collected.
    if in_block and block_lines:
        text = "\n".join(block_lines)
        comments.append((block_start, text))

    return comments


def _find_hash_comments(source: str, prefix: str) -> list[tuple[int, str]]:
    """Find comments prefixed by *prefix* (``#`` or ``--``).

    Detects both full-line and inline comments.
    """
    comments: list[tuple[int, str]] = []
    lines = source.splitlines(keepends=True)

    for i, line in enumerate(lines, start=1):
        cleaned = line.rstrip("\n").rstrip("\r")
        stripped = cleaned.strip()

        if not stripped:
            continue

        # Full-line comment.
        if stripped.startswith(prefix):
            comments.append((i, cleaned))
            continue

        # Inline comment: look for prefix outside of quotes.
        idx = _find_prefix_outside_quotes(cleaned, prefix)
        if idx is not None:
            comments.append((i, cleaned))

    return comments


# ---------------------------------------------------------------------------
# Comment-finding helpers
# ---------------------------------------------------------------------------


def _find_prefix_outside_quotes(line: str, prefix: str) -> int | None:
    """Find *prefix* in *line* when it occurs outside of quoted strings.

    Returns the index of the prefix, or ``None``.
    Uses a single-quote/double-quote state machine.
    """
    in_single = False
    in_double = False
    plen = len(prefix)
    i = 0
    while i < len(line) - plen + 1:
        ch = line[i]
        if ch == "'" and not in_double:
            # Check for escaped quote.
            if i > 0 and line[i - 1] == "\\":
                pass
            else:
                in_single = not in_single
        elif ch == '"' and not in_single:
            if i > 0 and line[i - 1] == "\\":
                pass
            else:
                in_double = not in_double
        elif not in_single and not in_double and line[i:i + plen] == prefix:
            # Must be preceded by whitespace or be at start of non-whitespace
            # (avoid matching ``#`` inside a word like ``C#`` or ``#pragma``).
            if i == 0 or line[i - 1] in (" ", "\t"):
                return i
            # Also handle inline case: ``code; # comment``
            if i > 0 and line[i - 1] in (";", "{", "("):
                return i
        i += 1
    return None


def _find_ts_inline_comment(line: str) -> int | None:
    """Find ``//`` that starts a comment in *line*.

    Returns the index of ``//``, or ``None``.
    Uses a simple state machine to avoid matching ``//`` inside strings and
    regex literals.
    """
    in_single = False
    in_double = False
    in_regex = False
    i = 0
    while i < len(line) - 1:
        ch = line[i]
        next_ch = line[i + 1]

        # Skip escaped characters.
        if ch == "\\":
            i += 2
            continue

        if ch == "'" and not in_double and not in_regex:
            in_single = not in_single
        elif ch == '"' and not in_single and not in_regex:
            in_double = not in_double
        elif ch == "/" and next_ch == "/" and not in_single and not in_double and not in_regex:
            return i

        # Heuristic for regex literals: /.../ after certain tokens.
        if ch == "/" and not in_single and not in_double:
            if i == 0 or line[i - 1] in (" ", "=", "(", "[", "{", "!", "&", "|", ":", ",", ";"):
                in_regex = not in_regex

        i += 1
    return None


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


def _any_match(patterns: list[re.Pattern[str]], text: str) -> bool:
    """Return ``True`` if any *patterns* match *text*."""
    return any(p.search(text) for p in patterns)


def _categorize(comment_text: str) -> str:
    """Assign a pre-classification category to *comment_text*.

    Priority ordering (first match wins):

    1. ``banner_label`` — lines of dashes/equals/stars.
    2. ``marketing`` — copyright, license, company names.
    3. ``meta_process`` — TODO, FIXME, HACK, etc.
    4. ``restate`` — starts with a restate verb.
    5. ``standard`` — default.
    """
    # Strip comment prefix for cleaner matching.
    clean = _strip_comment_prefix(comment_text)

    # Priority 1: Banner labels.
    if _BANNER_RE.match(clean):
        return BANNER_LABEL

    # Priority 2: Marketing.
    if _MARKETING_RE.search(clean):
        return MARKETING

    # Priority 3: Meta-process.
    if _any_match(_META_PATTERNS, clean):
        return META_PROCESS

    # Priority 4: Restate.
    if _RESTATE_RE.match(clean.lstrip()):
        return RESTATE

    # Priority 5: Standard (default).
    return STANDARD


def _strip_comment_prefix(text: str) -> str:
    """Strip comment prefix characters (``#``, ``//``, ``--``) and leading
    whitespace from *text*.
    """
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        s = line.strip()
        # Strip one layer of comment prefix.
        for prefix in ("#", "//", "--", "*"):
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        # For /* */ style, strip outer markers.
        if s.startswith("/*"):
            s = s[2:]
        if s.endswith("*/"):
            s = s[:-2]
        cleaned.append(s.strip())
    return " ".join(cleaned).strip()
