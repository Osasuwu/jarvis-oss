"""Personal-literal scrub — CI step for #23.

Reads a newline-separated list of private literals from the ``PERSONAL_LITERALS``
repository secret and fails the check if any literal appears anywhere in the
working tree, in any of its variants: case, separators and path forms (#100). An empty or unset list fails the check: it would otherwise report
clean while checking nothing. The list itself never lands in the tree, and matched output
names only the file path — never the literal value that matched, so a hit is
not re-leaked into the job log.

Run standalone in CI:

    python scripts/scrub_personal_literals.py

Reads the literal list from the ``PERSONAL_LITERALS`` env var and scans
``GITHUB_WORKSPACE`` (falling back to the current directory).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Characters treated as interchangeable separators between the words of a literal, including
# none at all: `Acme Verify`, `acme-verify`, `ACME_VERIFY`, `acme.verify` and `AcmeVerify` are
# one literal. `/`, `\` and `:` make the path forms of a path literal match each other too:
# `C:\Users\x`, `C:/Users/x`, `/c/Users/x` and a JSON-escaped `C:\\Users\\x`.
_SEPARATORS = r"\s\-_./\\:"
_SEPARATOR_RUN = re.compile(f"[{_SEPARATORS}]+")
_BETWEEN_WORDS = f"[{_SEPARATORS}]*"
# Word boundaries inside a separator-free run: camelCase, an acronym before a word, and a
# switch between letters and digits (`AcmeVerify`, `ACMEVerify`, `acme2`).
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])"
)


LITERALS_ENV_VAR = "PERSONAL_LITERALS"


def parse_literals(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def literals_from_env() -> list[str]:
    """The one literal source every gate reads: the CI scrub, the pre-push gate, the hook."""
    return parse_literals(os.environ.get(LITERALS_ENV_VAR, ""))


def literal_words(literal: str) -> list[str]:
    """Split a literal into the words its variants share: at separators and camelCase."""
    words: list[str] = []
    for run in _SEPARATOR_RUN.split(literal):
        words.extend(w for w in _CAMEL_BOUNDARY.split(run) if w)
    return words


def compile_literal_matcher(literals: list[str]) -> re.Pattern[str] | None:
    """One case-insensitive pattern matching every literal in any of its variants.

    Punctuation that is not a separator (`@`, `,`, ...) stays as written. An entry with no
    letter or digit is skipped: it would compile to a pattern that matches almost anything.
    Returns None when nothing is left to match; callers must treat that as "no list", never
    as "clean".
    """
    alternatives = []
    for literal in literals:
        words = literal_words(literal)
        if not any(ch.isalnum() for ch in "".join(words)):
            continue
        alternatives.append(_BETWEEN_WORDS.join(re.escape(w) for w in words))
    if not alternatives:
        return None
    return re.compile("|".join(f"(?:{a})" for a in alternatives), re.IGNORECASE)


def find_literal_hits(literals: list[str], root: Path) -> list[str]:
    matcher = compile_literal_matcher(literals)
    if matcher is None:
        return []
    hits: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for filename in filenames:
            file_path = Path(dirpath) / filename
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if matcher.search(content):
                hits.add(str(file_path.relative_to(root).as_posix()))
    return sorted(hits)


def run(literals: list[str], root: Path) -> int:
    if compile_literal_matcher(literals) is None:
        # An empty list checks nothing; reporting "clean" here is a gate that cannot fail (#41).
        # A list of only punctuation compiles to nothing too, and fails the same way.
        print(
            "No personal literals configured: set the PERSONAL_LITERALS repository secret "
            "(one literal per line). Nothing was checked, so this step fails."
        )
        return 1
    hits = find_literal_hits(literals, root)
    if hits:
        print("Personal literal(s) found in the following files (values withheld):")
        for path in hits:
            print(f"  {path}")
        return 1
    print(f"Scrub clean — checked {len(literals)} literal(s); none found in the tree.")
    return 0


def main() -> int:
    root = Path(os.environ.get("GITHUB_WORKSPACE", "."))
    return run(literals_from_env(), root)


if __name__ == "__main__":
    sys.exit(main())
