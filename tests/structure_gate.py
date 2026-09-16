"""Structure gate: validates the docs/examples/resources bucket frontmatter contract.

Schema source: Osasuwu/jarvis docs/decisions/2026-Q3.md (D18, D21, D24, D32, D34;
AC — jarvis-oss shape, locked 2026-09-15). The sign-off ledger check (D26) is a later
slice and is intentionally not implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

STALE_AFTER_DAYS = 180

# D24 describes the cap qualitatively ("the two-hour unit") with no numeric value recorded
# anywhere in the decision record. 20000 bytes (~roughly a 10-15 minute read) is a placeholder
# default, chosen here and flagged as revisable — see PR body.
DOC_SIZE_CAP_BYTES = 20_000

DOC_REQUIRED_KEYS = ("applies_when", "applies_when_not", "signed_off")
RESOURCE_REQUIRED_KEYS = ("pairs_with", "harnesses", "cost")

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


def _parse_last_seen_date(value: str) -> date | None:
    token = value.strip().split()[-1]
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _check_docs(root: Path) -> list[Violation]:
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return []
    violations: list[Violation] = []
    for doc_path in sorted(docs_dir.rglob("*.md")):
        text = doc_path.read_text(encoding="utf-8")
        fields = _parse_frontmatter(text)
        rel = _rel(doc_path, root)
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


def _check_examples(root: Path) -> list[Violation]:
    examples_dir = root / "examples"
    if not examples_dir.is_dir():
        return []
    violations: list[Violation] = []
    for example_path in sorted(examples_dir.rglob("*.md")):
        fields = _parse_frontmatter(example_path.read_text(encoding="utf-8"))
        rel = _rel(example_path, root)
        if "fit" not in fields:
            violations.append(
                Violation(
                    path=rel,
                    code="example_missing_key:fit",
                    message=f"{rel} is missing required frontmatter key 'fit'",
                )
            )
        has_own_provenance = "last_seen" in fields
        has_external_provenance = "source" in fields and "verified" in fields
        if not has_own_provenance and not has_external_provenance:
            violations.append(
                Violation(
                    path=rel,
                    code="example_missing_provenance",
                    message=(
                        f"{rel} needs either 'last_seen' (own example) or "
                        "'source'+'verified' (external example)"
                    ),
                )
            )
        if has_own_provenance:
            last_seen_date = _parse_last_seen_date(fields["last_seen"])
            if last_seen_date is not None and date.today() - last_seen_date > timedelta(
                days=STALE_AFTER_DAYS
            ):
                violations.append(
                    Violation(
                        path=rel,
                        code="example_last_seen_stale",
                        message=(
                            f"{rel} last_seen {last_seen_date.isoformat()} is older than "
                            f"{STALE_AFTER_DAYS} days"
                        ),
                    )
                )
    return violations


def _check_resources(root: Path) -> list[Violation]:
    resources_dir = root / "resources"
    if not resources_dir.is_dir():
        return []
    violations: list[Violation] = []
    for resource_path in sorted(resources_dir.rglob("*.md")):
        fields = _parse_frontmatter(resource_path.read_text(encoding="utf-8"))
        rel = _rel(resource_path, root)
        for key in RESOURCE_REQUIRED_KEYS:
            if key not in fields:
                violations.append(
                    Violation(
                        path=rel,
                        code=f"resource_missing_key:{key}",
                        message=f"{rel} is missing required frontmatter key '{key}'",
                    )
                )
        pairs_with = fields.get("pairs_with")
        if pairs_with and not (root / pairs_with).is_file():
            violations.append(
                Violation(
                    path=rel,
                    code="pairs_with_unresolvable",
                    message=f"{rel} pairs_with '{pairs_with}' does not resolve to a file",
                )
            )
    return violations


def check_tree(root: Path) -> list[Violation]:
    root = Path(root)
    violations: list[Violation] = []
    violations.extend(_check_docs(root))
    violations.extend(_check_examples(root))
    violations.extend(_check_resources(root))
    return violations
