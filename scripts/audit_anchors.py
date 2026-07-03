"""Audit markdown anchor links across the repo — find dead refs.

L1: String-not-found anchors + broken cross-file paths (CI-enforced)
L2: Suffixed-N anchor drift (informational punch-list)
L3: Line-number annotations in link text (CI-enforced with regex)

Usage (CLI):
  python scripts/audit_anchors.py  -> prints broken + L2 punch-list, exits 0 or 1

Usage (import):
  from scripts.audit_anchors import get_corpus, find_broken_links, find_line_number_annotations
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent

# Regex patterns
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
ANCHOR_INLINE_RE = re.compile(r'<a\s+(?:id|name)="([^"]+)"', re.IGNORECASE)
GH_RELATIVE_RE = re.compile(r"^\.\./\.\./(?:security|issues|pulls|wiki|releases)/")
FENCE_DELIMITER = re.compile(r"^```(?:[a-z]*)?$", re.MULTILINE)
LINE_NUMBER_ANNOTATION_RE = re.compile(r"\[[^\]]*\([lL]ines?\s+\d+")


def slugify(heading: str) -> str:
    """GitHub-style heading -> anchor slug.

    Order matters: lowercase -> strip inline markdown -> resolve link syntax ->
    replace whitespace 1:1 with '-' -> drop anything not [a-z0-9_-].
    Spaces-first preserves consecutive dashes that come from punctuation
    (e.g. "C17 — Observability" -> "c17--observability").
    """
    s = heading.lower()
    # Strip inline code/bold/italics markers
    s = re.sub(r"[`*]", "", s)
    # Resolve `[text](url)` -> `text`
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    # Replace each whitespace char with one '-'
    s = re.sub(r"\s", "-", s)
    # Drop anything not alnum, '-', or '_'
    s = re.sub(r"[^a-z0-9_\-]", "", s)
    return s.strip("-")


def collect_anchors(text: str) -> set[str]:
    """Return the set of GitHub-style anchor slugs for headings in text.

    Duplicates get a -1, -2, ... suffix in document order (GitHub behavior).
    Skips lines inside fenced code blocks (``` ```).
    """
    # Split by fence delimiters, then parse only odd-indexed chunks (outside fences)
    parts = FENCE_DELIMITER.split(text)
    parseable_text = "\n".join(parts[i] for i in range(0, len(parts), 2))

    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for _, heading in HEADING_RE.findall(parseable_text):
        clean = re.sub(r"<[^>]+>", "", heading)
        base = slugify(clean)
        n = counts.get(base, 0)
        anchors.add(base if n == 0 else f"{base}-{n}")
        counts[base] = n + 1
    for inline in ANCHOR_INLINE_RE.findall(parseable_text):
        anchors.add(inline.lower())
    return anchors


def is_github_relative_path(path: str) -> bool:
    """Check if a path is a GitHub-relative URL (issues/, pulls/, etc.)."""
    return GH_RELATIVE_RE.match(path) is not None


def find_broken_links(corpus: dict[Path, str]) -> list[tuple[Path, int, str, str]]:
    """Find broken anchor and cross-file references in corpus.

    Returns list of (file, lineno, label, target) tuples.
    Skips lines inside fenced code blocks.
    corpus: dict mapping Path -> text content
    """
    anchors_by_file: dict[Path, set[str]] = {}
    for f, text in corpus.items():
        anchors_by_file[f] = collect_anchors(text)

    broken: list[tuple[Path, int, str, str]] = []
    for f, text in corpus.items():
        # Build a set of line numbers that are inside fences
        parts = FENCE_DELIMITER.split(text)
        inside_fence_lines: set[int] = set()
        line_offset = 0
        for i, part in enumerate(parts):
            is_inside_fence = (i % 2 == 1)  # Odd indices are inside fences
            part_lines = part.count('\n')
            if is_inside_fence:
                for j in range(part_lines + 1):
                    inside_fence_lines.add(line_offset + j)
            line_offset += part_lines + 1  # +1 for the fence delimiter line itself

        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip if inside fence
            if lineno in inside_fence_lines:
                continue

            for label, target in LINK_RE.findall(line):
                # Skip external URLs and mailto/tel
                if target.startswith(("http://", "https://", "mailto:", "tel:")):
                    continue

                # Skip GitHub-relative paths (resolved on github.com, not locally)
                if is_github_relative_path(target):
                    continue

                if target.startswith("#"):
                    # Same-file anchor
                    anchor = target[1:].lower()
                    if anchor and anchor not in anchors_by_file[f]:
                        broken.append((f, lineno, label, target))
                    continue

                # Has anchor or pure path
                if "#" in target:
                    path_part, anchor = target.split("#", 1)
                else:
                    path_part, anchor = target, ""

                if not path_part:
                    continue

                resolved = (f.parent / path_part).resolve()
                # Check if file exists
                if not resolved.exists():
                    broken.append((f, lineno, label, target))
                    continue

                if anchor:
                    if resolved.suffix.lower() != ".md":
                        continue  # anchor in non-md (e.g., html) — skip
                    if resolved not in anchors_by_file:
                        # Out-of-scope md, parse on-demand
                        try:
                            anchors_by_file[resolved] = collect_anchors(
                                resolved.read_text(encoding="utf-8", errors="replace")
                            )
                        except Exception:
                            continue
                    if anchor.lower() not in anchors_by_file[resolved]:
                        broken.append((f, lineno, label, target))

    return broken


def find_line_number_annotations(corpus: dict[Path, str]) -> list[tuple[Path, int, str]]:
    """Find line-number annotations inside link text (L3).

    Returns list of (file, lineno, text) tuples for manual review.
    """
    found: list[tuple[Path, int, str]] = []
    for f, text in corpus.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            if LINE_NUMBER_ANNOTATION_RE.search(line):
                found.append((f, lineno, line))
    return found


def get_corpus() -> dict[Path, str]:
    """Get the audit corpus: all tracked *.md files except docs/research/.

    Uses `git ls-files` to get tracked files, filters to *.md, excludes
    docs/research/, returns dict of Path -> text.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: direct glob if git fails
        result_files = sorted(ROOT.glob("**/*.md"))
        corpus = {}
        for f in result_files:
            if "docs/research" in f.parts or ".research" in str(f):
                continue
            try:
                corpus[f] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        return corpus

    corpus: dict[Path, str] = {}
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        f = ROOT / line
        # Skip docs/research/
        if "docs/research" in f.parts or ".research" in str(f):
            continue
        try:
            corpus[f] = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return corpus


def main() -> int:
    """CLI entry point."""
    corpus = get_corpus()
    broken = find_broken_links(corpus)
    line_annotations = find_line_number_annotations(corpus)

    if not broken and not line_annotations:
        print("OK — no broken anchor/path references (excluding docs/research/)")
        # L2 sanity: enumerate all suffixed-N anchor links
        suffix_re = re.compile(r"-(\d+)$")
        suffixed: list[tuple[Path, int, str, str, int]] = []
        for f, text in corpus.items():
            for lineno, line in enumerate(text.splitlines(), 1):
                for label, target in LINK_RE.findall(line):
                    if not target.startswith("#") and "#" not in target:
                        continue
                    anchor = target.split("#", 1)[1].lower() if "#" in target else ""
                    if not anchor:
                        continue
                    m = suffix_re.search(anchor)
                    if m and int(m.group(1)) >= 1:
                        suffixed.append((f, lineno, label, target, int(m.group(1))))
        if suffixed:
            print(f"\nL2 sanity audit (#662): {len(suffixed)} suffixed-N anchor link(s) — manual-review punch list:\n")
            by_file: dict[Path, list[tuple[int, str, str, int]]] = {}
            for f, lineno, label, target, n in suffixed:
                by_file.setdefault(f, []).append((lineno, label, target, n))
            for f in sorted(by_file):
                rel = f.relative_to(ROOT)
                print(f"## {rel}")
                for lineno, label, target, n in by_file[f]:
                    print(f"  L{lineno}: -{n} suffix  [{label}]({target})")
                print()
        else:
            print("L2 sanity audit (#662): 0 suffixed-N anchor links — class bounded.")
        return 0

    # Broken anchors or line annotations found
    if broken:
        print(f"Found {len(broken)} broken reference(s):\n")
        by_file: dict[Path, list[tuple[int, str, str]]] = {}
        for f, lineno, label, target in broken:
            by_file.setdefault(f, []).append((lineno, label, target))
        for f in sorted(by_file):
            rel = f.relative_to(ROOT)
            print(f"## {rel}")
            for lineno, label, target in by_file[f]:
                print(f"  L{lineno}: [{label}]({target})")
            print()

    if line_annotations:
        print(f"Found {len(line_annotations)} line-number annotation(s) in link text (L3):\n")
        for f, lineno, line in line_annotations:
            rel = f.relative_to(ROOT)
            print(f"## {rel}")
            print(f"  L{lineno}: {line.strip()}")
            print()

    return 1


if __name__ == "__main__":
    sys.exit(main())
