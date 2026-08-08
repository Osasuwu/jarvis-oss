#!/usr/bin/env python3
"""Validate that the CONTEXT.md glossary index is in sync with section entries.

Scans the `## Glossary` section:
  - Reads indexed entries under `### Index`
  - Reads actual entries under every other `###` category
  - Reports mismatches in either direction
  - Reports terms defined (or indexed) more than once
  - Exits 0 if consistent, 1 if not

Usage:
    python scripts/check-glossary-index.py             # default path
    python scripts/check-glossary-index.py --file <path>
"""

import argparse
import re
import sys
from collections import Counter


_TERM_RE = re.compile(r'^-\s+\*\*(.+?)\*\*')


def _normalize(name: str) -> str:
    """Normalize a term name for comparison between index and sections."""
    name = name.strip()
    name = name.replace('`', '')
    # Strip leading heading markers inside bold text
    name = re.sub(r'^#+\s*', '', name)
    # Strip trailing description after em-dash (never part of term name)
    name = re.sub(r'\s*[—–]\s*.*$', '', name)
    # Strip trailing descriptive suffixes
    name = re.sub(r'\s+(?:repo variable)$', '', name)
    # Strip trailing parenthetical + = patterns + period
    name = re.sub(
        r'\s*(?:\(#[A-Za-z0-9_-]+\)|\([^)]{1,60}\))?\s*'
        r'(?:=.*)?\.?\s*$',
        '', name,
    )
    return name.strip()


def _duplicates(terms: list[str]) -> list[str]:
    """Return the sorted names appearing more than once in `terms`."""
    return sorted(t for t, n in Counter(terms).items() if n > 1)


def _parse_index_terms(text: str) -> list[str]:
    """Return indexed term names under ### Index, in order, duplicates kept."""
    m = re.search(r'^### Index\s*$.*?(?=^### |\Z)', text, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    terms: list[str] = []
    for line in m.group(0).splitlines():
        entry = _TERM_RE.match(line.strip())
        if entry:
            terms.append(_normalize(entry.group(1)))
    return terms


def _parse_section_entries(text: str) -> list[str]:
    """Return entry names under every ### category EXCEPT Index, duplicates kept.

    Returns a list rather than a set on purpose: a term defined twice is the
    one drift class this file has actually suffered (51fce34 removed a
    duplicated glossary block by hand). A set collapses the second copy and
    reports the file as clean, so the checker would be structurally blind to
    a repeat of the very incident it exists to catch.
    """
    sections = re.split(r'^### ', text, flags=re.MULTILINE)
    terms: list[str] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        lines = sec.splitlines()
        heading = lines[0].strip() if lines else ""
        if heading == "Index":
            continue
        for line in lines:
            entry = _TERM_RE.match(line)
            if entry:
                terms.append(_normalize(entry.group(1)))
    return terms


def check(text: str) -> int:
    """Return 0 if index and entries are in sync, 1 otherwise."""
    # Narrow to content between ## Glossary and the next ## heading (or end of file)
    m = re.search(r'^## Glossary\s*$.*?(?=^## |\Z)', text, re.MULTILINE | re.DOTALL)
    if not m:
        print("ERROR: ## Glossary section not found", file=sys.stderr)
        return 1
    glossary_body = m.group(0)

    indexed_list = _parse_index_terms(glossary_body)
    actual_list = _parse_section_entries(glossary_body)
    indexed = set(indexed_list)
    actual = set(actual_list)

    if not indexed:
        print("ERROR: no entries found under ### Index", file=sys.stderr)
        return 1
    if not actual:
        print("ERROR: no entries found in glossary sections", file=sys.stderr)
        return 1

    missing_from_index = sorted(actual - indexed)
    missing_from_bodies = sorted(indexed - actual)
    dup_in_bodies = _duplicates(actual_list)
    dup_in_index = _duplicates(indexed_list)
    n_issues = 0

    if missing_from_index:
        print("ERROR: entries in sections but NOT in index:")
        for t in missing_from_index:
            print(f"  - {t}")
        n_issues += 1

    if missing_from_bodies:
        print("ERROR: entries in index but NOT in any section:")
        for t in missing_from_bodies:
            print(f"  - {t}")
        n_issues += 1

    # Set-difference above is blind to duplication: a term defined twice is
    # present in both directions and reads as in-sync. Duplicated blocks are
    # the drift this file already suffered once (51fce34), so they get their
    # own check.
    if dup_in_bodies:
        print("ERROR: terms defined more than once across glossary sections:")
        for t in dup_in_bodies:
            print(f"  - {t}")
        n_issues += 1

    if dup_in_index:
        print("ERROR: terms listed more than once under ### Index:")
        for t in dup_in_index:
            print(f"  - {t}")
        n_issues += 1

    if n_issues == 0:
        print(f"OK: {len(indexed)} indexed entries match {len(actual)} section entries")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="CONTEXT.md")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        text = f.read()

    return check(text)


if __name__ == "__main__":
    sys.exit(main())
