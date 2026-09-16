"""Personal-literal scrub — CI step for #23 (D25, Osasuwu/jarvis docs/decisions/2026-Q3.md).

Reads a newline-separated list of private literals from the
``PERSONAL_LITERALS`` repository secret and fails the check if any literal
appears anywhere in the working tree. The list itself never lands in the
tree, and matched output names only the file path — never the literal value
that matched, so a hit is not re-leaked into the job log.

Run standalone in CI:

    python scripts/scrub_personal_literals.py

Reads the literal list from the ``PERSONAL_LITERALS`` env var and scans
``GITHUB_WORKSPACE`` (falling back to the current directory).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Directories never worth scanning — VCS internals plus common build/venv
#: noise mirrored from .gitleaks.toml's allowlist.
EXCLUDED_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}


def parse_literals(raw: str) -> list[str]:
    """Split a newline-separated literal list, dropping blank/whitespace-only lines."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


def find_literal_hits(literals: list[str], root: Path) -> list[str]:
    """Return sorted repo-relative paths of files containing any literal.

    Each file is listed at most once, regardless of how many literals or how
    many times a literal occurs in it — the caller only needs to know where
    to look, not what matched.
    """
    if not literals:
        return []

    hits: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                continue
            if any(literal in content for literal in literals):
                hits.add(str(file_path.relative_to(root).as_posix()))

    return sorted(hits)


def run(literals: list[str], root: Path) -> int:
    """Scan ``root`` for ``literals`` and print a report. Returns the process exit code."""
    if not literals:
        print(
            "No literal list configured (PERSONAL_LITERALS secret unset or empty) — skipping scrub."
        )
        return 0

    hits = find_literal_hits(literals, root)
    if hits:
        print("Personal literal(s) found in the following files (values withheld):")
        for path in hits:
            print(f"  {path}")
        return 1

    print("Scrub clean — no personal literals found in the tree.")
    return 0


def main() -> int:
    raw = os.environ.get("PERSONAL_LITERALS", "")
    root = Path(os.environ.get("GITHUB_WORKSPACE", "."))
    return run(parse_literals(raw), root)


if __name__ == "__main__":
    sys.exit(main())
