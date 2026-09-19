"""Structure gate: validates the docs/examples/resources bucket frontmatter contract.

Schema source: Osasuwu/jarvis docs/decisions/2026-Q3.md (D18, D21, D24, D26, D32, D34;
AC — jarvis-oss shape, locked 2026-09-15). D26's "floor and expiry" behavior (a body change
after signed_off clears sign-off) is out of scope here — tracked separately.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

STALE_AFTER_DAYS = 180

SIGNOFF_LEDGER_PATH = "docs/SIGNOFF.md"

# Ledger entry line: "- `<repo-relative doc path>`: <signed_off date>; facts: <human | report URL>"
# The facts part says who checked facts and completeness (#59): the signer, or a review-doc
# report. A line without it still parses, so it is reported as missing facts, not as absent.
_SIGNOFF_ENTRY_RE = re.compile(
    r"^-\s*`([^`]+)`:\s*(\d{4}-\d{2}-\d{2})\s*(?:;\s*facts:\s*(human|https://\S+))?\s*$"
)

# D24 describes the cap qualitatively ("the two-hour unit") with no numeric value recorded
# anywhere in the decision record. Raised from the 20_000-byte placeholder to 30_000
# (~roughly a 15-20 minute read) per #78: the placeholder was hit four times in two days
# (#66, #73, #76, #74), and trimming content to fit produced defects (#73) that no review
# pass caught, so the grill on #74 (2026-09-17) decided to raise the cap instead of cutting.
DOC_SIZE_CAP_BYTES = 30_000

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
        rel = _rel(doc_path, root)
        if rel == SIGNOFF_LEDGER_PATH:
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


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout


def _last_commit_for(root: Path, rel_path: str) -> str | None:
    output = _git(root, "log", "-n", "1", "--format=%H", "--", rel_path)
    if not output:
        return None
    return output.strip() or None


def _commit_for_ledger_entry(root: Path, doc_rel: str) -> str | None:
    needle = f"`{doc_rel}`:"
    output = _git(root, "log", f"-S{needle}", "--format=%H", "--", SIGNOFF_LEDGER_PATH)
    if not output:
        return None
    hashes = [line for line in output.splitlines() if line.strip()]
    if not hashes:
        return None
    return hashes[0]


def _parse_signoff_ledger(text: str) -> dict[str, tuple[str, str | None]]:
    entries: dict[str, tuple[str, str | None]] = {}
    for line in text.splitlines():
        match = _SIGNOFF_ENTRY_RE.match(line.strip())
        if match:
            entries[match.group(1)] = (match.group(2), match.group(3))
    return entries


def _check_signoff(root: Path) -> list[Violation]:
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return []
    ledger_path = root / SIGNOFF_LEDGER_PATH
    ledger_entries = (
        _parse_signoff_ledger(ledger_path.read_text(encoding="utf-8"))
        if ledger_path.is_file()
        else {}
    )
    violations: list[Violation] = []
    for doc_path in sorted(docs_dir.rglob("*.md")):
        rel = _rel(doc_path, root)
        if rel == SIGNOFF_LEDGER_PATH:
            continue
        fields = _parse_frontmatter(doc_path.read_text(encoding="utf-8"))
        signed_off = fields.get("signed_off")
        if not signed_off:
            continue
        entry_date, facts = ledger_entries.get(rel, (None, None))
        if entry_date != signed_off:
            violations.append(
                Violation(
                    path=rel,
                    code="signoff_missing_entry",
                    message=(
                        f"{rel} has signed_off '{signed_off}' with no matching "
                        f"{SIGNOFF_LEDGER_PATH} entry"
                    ),
                )
            )
            continue
        if facts is None:
            violations.append(
                Violation(
                    path=rel,
                    code="signoff_missing_facts",
                    message=(
                        f"{rel}'s {SIGNOFF_LEDGER_PATH} entry does not say who checked facts: "
                        "end it with '; facts: human' or '; facts: <review-doc report URL>'"
                    ),
                )
            )
        doc_commit = _last_commit_for(root, rel)
        ledger_commit = _commit_for_ledger_entry(root, rel)
        if doc_commit is not None and doc_commit == ledger_commit:
            violations.append(
                Violation(
                    path=rel,
                    code="signoff_same_commit",
                    message=(
                        f"{rel}'s {SIGNOFF_LEDGER_PATH} entry was added in the same "
                        f"commit ({doc_commit[:8]}) as the doc body; it must be a "
                        "separate commit"
                    ),
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
        if "pairs_with" not in fields:
            violations.append(
                Violation(
                    path=rel,
                    code="example_missing_key:pairs_with",
                    message=f"{rel} is missing required frontmatter key 'pairs_with'",
                )
            )
        violations.extend(_check_pairs_with(root, rel, fields))
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


def _check_pairs_with(root: Path, rel: str, fields: dict[str, str]) -> list[Violation]:
    # pairs_with may name several docs, comma-separated; each one must resolve.
    targets = [t.strip() for t in fields.get("pairs_with", "").split(",") if t.strip()]
    return [
        Violation(
            path=rel,
            code="pairs_with_unresolvable",
            message=f"{rel} pairs_with '{target}' does not resolve to a file",
        )
        for target in targets
        if not (root / target).is_file()
    ]


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
        violations.extend(_check_pairs_with(root, rel, fields))
    return violations


def check_tree(root: Path) -> list[Violation]:
    root = Path(root)
    violations: list[Violation] = []
    violations.extend(_check_docs(root))
    violations.extend(_check_signoff(root))
    violations.extend(_check_examples(root))
    violations.extend(_check_resources(root))
    return violations
