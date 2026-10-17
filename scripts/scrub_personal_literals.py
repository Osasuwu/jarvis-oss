"""Personal-literal scrub — CI step for #23.

Reads a newline-separated list of private literals from the ``PERSONAL_LITERALS``
repository secret and fails the check if any literal appears anywhere in the
working tree, in any of its variants: case, separators and path forms (#100). A file's
path is checked as well as its content, so a literal in a file or directory name fails too
(#84). Blank lines and lines starting with ``#`` in the list are skipped. An empty or unset
list fails the check: it would otherwise report clean while checking nothing. The list itself
never lands in the tree, and the job log is public, so output never contains a literal: a hit
names the file, with every literal in its path replaced by ``[redacted]`` (or the path shown
as a short sha256 when that is not enough), and never the literal value that matched.

Run standalone in CI:

    python scripts/scrub_personal_literals.py

Reads the literal list from the ``PERSONAL_LITERALS`` env var and scans
``GITHUB_WORKSPACE`` (falling back to the current directory). It walks the checkout including
``.git``. A hit prints the redacted path under "values withheld"; an empty list, or one with
no letter or digit, fails.

Not covered: packed history, commit messages and PR or issue text (the pre-push gate and the
literal-gate hook cover those before they leave the machine), typos and literals split across
lines, and fork pull requests (GitHub withholds secrets from them). Keep each literal specific
enough not to occur by chance, or every pull request goes red.

Install (a step in a CI job, Python 3.12):

1. Copy this script and its step. Pass the secret only to that step, after the secret scanner::

       - name: Scrub personal literals
         env:
           PERSONAL_LITERALS: ${{ secrets.PERSONAL_LITERALS }}
         run: python scripts/scrub_personal_literals.py

2. Set the secret from a file outside the repo: ``gh secret set PERSONAL_LITERALS < list.txt``.
3. Make the job a required check, or a red run blocks nothing.

Check:

- A run that checked something ends ``Scrub clean — checked N literal(s); none found in the
  tree.`` (N = secret lines that are neither blank nor ``#`` comments). An older line without a count proves nothing: it
  printed with no list at all.
- With no list the step fails with "No personal literals configured…"; expected on an unedited
  fork pull request.
- Never plant a real entry: the branch is public once pushed. Add a random canary
  (``canary-7f3c9a``) to the secret, put it in a file on a throwaway branch and open a pull
  request. The step must fail and name the file. If it passes, the secret is not what you think.
"""

from __future__ import annotations

import hashlib
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
    """One literal per line. Blank lines and lines starting with `#` (after leading
    whitespace) are skipped, so the list can carry notes; a literal cannot start with `#`."""
    literals = []
    for line in raw.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            literals.append(entry)
    return literals


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
            relative = file_path.relative_to(root).as_posix()
            # The path is checked as well as the content: a literal in a file or directory
            # name lands in the public tree just the same (#84).
            if matcher.search(relative):
                hits.add(relative)
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if matcher.search(content):
                hits.add(relative)
    return sorted(hits)


REDACTED = "[redacted]"


def display_path(path: str, matcher: re.Pattern[str]) -> str:
    """The path as the public log may show it: every literal replaced by ``[redacted]``.

    If the result still matches (the marker plus its neighbours spell a literal), the path is
    shown as a short sha256 of itself instead.
    """
    redacted = matcher.sub(REDACTED, path)
    if matcher.search(redacted) is None:
        return redacted
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest} (path withheld)"


def run(literals: list[str], root: Path) -> int:
    matcher = compile_literal_matcher(literals)
    if matcher is None:
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
            print(f"  {display_path(path, matcher)}")
        return 1
    print(f"Scrub clean — checked {len(literals)} literal(s); none found in the tree.")
    return 0


def main() -> int:
    root = Path(os.environ.get("GITHUB_WORKSPACE", "."))
    return run(literals_from_env(), root)


if __name__ == "__main__":
    sys.exit(main())
