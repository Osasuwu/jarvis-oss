"""Structure gate: checks the docs under docs/ for the frontmatter keys, the size cap and links
that resolve to a file.

The knowledge-base contract (ADR-0004 decision 3) replaces the old kind/requires/hub/options/
check-date rules in a later slice; until then only the checks below run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Directories under docs/ that hold records, not reader-facing docs: decision records (#144).
EXCLUDED_DOC_DIRS = ("docs/adr/",)


def is_excluded_doc(path: str) -> bool:
    """Every file under an excluded directory."""
    return path.startswith(EXCLUDED_DOC_DIRS)


# D24 describes the cap qualitatively ("the two-hour unit") with no numeric value recorded
# anywhere in the decision record. Raised from the 20_000-byte placeholder to 30_000
# (~roughly a 15-20 minute read) per #78: the placeholder was hit four times in two days
# (#66, #73, #76, #74), and trimming content to fit produced defects (#73) that no review
# pass caught, so the grill on #74 (2026-09-17) decided to raise the cap instead of cutting.
DOC_SIZE_CAP_BYTES = 30_000

DOC_REQUIRED_KEYS = ("applies_when", "applies_when_not")

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class Violation:
    path: str
    code: str
    message: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    block = text[4:end]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _check_docs(root: Path) -> list[Violation]:
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return []
    violations: list[Violation] = []
    for doc_path in sorted(docs_dir.rglob("*.md")):
        rel = _rel(doc_path, root)
        if is_excluded_doc(rel):
            continue
        text = doc_path.read_text(encoding="utf-8")
        fields = _parse_frontmatter(text)
        size = len(text.encode("utf-8"))
        if size > DOC_SIZE_CAP_BYTES:
            violations.append(
                Violation(
                    path=rel,
                    code="doc_over_size_cap",
                    message=f"{rel} is {size} bytes, over the {DOC_SIZE_CAP_BYTES}-byte cap",
                )
            )
        for key in DOC_REQUIRED_KEYS:
            if key not in fields:
                violations.append(
                    Violation(
                        path=rel,
                        code=f"doc_missing_key:{key}",
                        message=f"{rel} is missing required frontmatter key '{key}'",
                    )
                )
        for link in _MARKDOWN_LINK_RE.findall(text):
            if "://" in link or link.startswith(("#", "mailto:")):
                continue
            target = (doc_path.parent / link).resolve()
            if not target.is_file():
                violations.append(
                    Violation(
                        path=rel,
                        code="boundary_evidence_unresolvable",
                        message=f"{rel} references '{link}', which does not resolve to a file",
                    )
                )
    return violations


def check_tree(root: Path) -> list[Violation]:
    return _check_docs(Path(root))
