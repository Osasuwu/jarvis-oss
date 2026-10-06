"""Structure gate: the class-doc contract of ADR-0004 (decisions 2, 3, 4, 11, 13, 16, 17).

Checks the class docs and the incident dataset in a tree. Three inputs are read from the scanned
root, never hard-coded here:

- `.github/hold-paths.json` — the one path definition (ADR-0004 decision 4). The
  `waiting-human-review` workflow reads the same file, and a test fails if the two disagree.
- `docs/vocabularies.json` — the stage, evidence-strength and surfaces-at vocabularies.
- `DOC_SIZE_CAP_BYTES` — the one configured size cap.

The gate is calibration-era: it changes with the contract until the freeze after the third
class doc. It checks structure only; ladder order, injection payloads and Evidence dates are out
of scope for it.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

# D24 describes the cap qualitatively ("the two-hour unit") with no numeric value recorded
# anywhere in the decision record. Raised from the 20_000-byte placeholder to 30_000
# (~roughly a 15-20 minute read) per #78: the placeholder was hit four times in two days
# (#66, #73, #76, #74), and trimming content to fit produced defects (#73) that no review
# pass caught, so the grill on #74 (2026-09-17) decided to raise the cap instead of cutting.
DOC_SIZE_CAP_BYTES = 30_000

HOLD_PATHS_FILE = ".github/hold-paths.json"
VOCABULARIES_FILE = "docs/vocabularies.json"
CLASS_DOCS_DIR = "docs/classes"
DATASET_DIR = "incidents"
DATASET_FILE = "incidents/incidents.csv"

DATASET_COLUMNS = ("id", "class", "stage", "source_type", "evidence_strength", "link")
VOCABULARY_KEYS = ("stage", "evidence_strength", "surfaces_at")

REQUIRED_KEYS = ("class", "surfaces_at", "applies_when", "applies_when_not")
REQUIRED_SECTIONS = (
    "TL;DR",
    "Symptom",
    "Examples",
    "Mechanism",
    "Where it surfaces",
    "Protections",
    "Evidence",
)
CROSS_CUTTING = "cross-cutting"
EXAMPLES_MIN, EXAMPLES_MAX = 2, 5

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_INCIDENT_ID_RE = re.compile(r"\bINC-\d{3,}\b")
_ID_RE = re.compile(r"INC-\d{3,}")
_SECTION_RE = re.compile(r"^## +(.+?) *$", re.MULTILINE)
_RUNG_RE = re.compile(r"^### +(.+?) *$", re.MULTILINE)
# A cost line that states a figure must carry "(as of YYYY-MM, <source>)".
_DATED_SOURCE_RE = re.compile(r"\(as of \d{4}-(?:0[1-9]|1[0-2]), *[^)\s][^)]*\)")


@dataclass(frozen=True)
class Violation:
    path: str
    code: str
    message: str


def is_hold_path(path: str, definition: dict) -> bool:
    """The path definition: a listed file is held; else a path under an excluded prefix is not;
    else a path under a listed prefix is. The hold workflow's script mirrors this order."""
    if path in definition["files"]:
        return True
    if path.startswith(tuple(definition["excluded_prefixes"])):
        return False
    return path.startswith(tuple(definition["prefixes"]))


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_json(root: Path, rel: str, violations: list[Violation]):
    path = root / rel
    if not path.is_file():
        violations.append(
            Violation(rel, "required_file_missing", f"{rel} is missing; the gate reads it")
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        violations.append(Violation(rel, "required_file_invalid", f"{rel} is not JSON: {exc}"))
        return None


def _is_word_list(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(v, str) and v.strip() for v in value)
    )


def _load_hold_paths(root: Path, violations: list[Violation]) -> dict | None:
    definition = _load_json(root, HOLD_PATHS_FILE, violations)
    if definition is None:
        return None
    for key in ("prefixes", "excluded_prefixes", "files"):
        if not isinstance(definition.get(key), list):
            violations.append(
                Violation(
                    HOLD_PATHS_FILE,
                    f"hold_paths_invalid:{key}",
                    f"{HOLD_PATHS_FILE} needs '{key}' as a list",
                )
            )
            return None
    return definition


def _load_vocabularies(root: Path, violations: list[Violation]) -> dict | None:
    vocab = _load_json(root, VOCABULARIES_FILE, violations)
    if vocab is None:
        return None
    ok = True
    for key in VOCABULARY_KEYS:
        if not _is_word_list(vocab.get(key)):
            violations.append(
                Violation(
                    VOCABULARIES_FILE,
                    f"vocabulary_invalid:{key}",
                    f"{VOCABULARIES_FILE} needs '{key}' as a non-empty list of words",
                )
            )
            ok = False
    return vocab if ok else None


def _parse_frontmatter(text: str) -> dict[str, str | list[str]]:
    """Flat `key: value` lines; `key: [a, b]` and a block list of `- item` lines give a list."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    fields: dict[str, str | list[str]] = {}
    current: str | None = None
    for line in text[4:end].splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line.lstrip().startswith("- ") and current is not None and line[0] in " -":
            existing = fields[current]
            items = existing if isinstance(existing, list) else []
            items.append(line.lstrip()[2:].strip())
            fields[current] = items
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        current = key
        if value.startswith("[") and value.endswith("]"):
            fields[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif value == "":
            fields[key] = []  # a block list follows; stays empty (and so invalid) if none does
        else:
            fields[key] = value
    return fields


def _check_dataset(
    root: Path, vocab: dict | None, violations: list[Violation]
) -> dict[str, str] | None:
    """Check the dataset's rows; return {id: class} of the rows that carry both, or None when
    the dataset cannot be read."""
    path = root / DATASET_FILE
    if not path.is_file():
        violations.append(
            Violation(
                DATASET_FILE,
                "required_file_missing",
                f"{DATASET_FILE} is missing; the gate reads it",
            )
        )
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        missing = [c for c in DATASET_COLUMNS if c not in header]
        for column in missing:
            violations.append(
                Violation(
                    DATASET_FILE,
                    f"dataset_missing_column:{column}",
                    f"{DATASET_FILE} has no '{column}' column",
                )
            )
        if missing:
            return None
        classes: dict[str, str] = {}
        seen: set[str] = set()
        for number, row in enumerate(reader, start=2):
            if not row:
                continue
            fields = dict(zip(header, row))
            where = f"{DATASET_FILE} row {number}"
            for column in DATASET_COLUMNS:
                if not fields.get(column):
                    violations.append(
                        Violation(
                            DATASET_FILE,
                            f"row_missing_value:{column}",
                            f"{where}: no value for '{column}'",
                        )
                    )
            incident = fields.get("id", "")
            if incident and not _ID_RE.fullmatch(incident):
                violations.append(
                    Violation(DATASET_FILE, "row_bad_id", f"{where}: id '{incident}' is not INC-<digits>")
                )
            if incident and incident in seen:
                violations.append(
                    Violation(DATASET_FILE, "row_duplicate_id", f"{where}: id '{incident}' repeats")
                )
            seen.add(incident)
            if vocab is not None:
                for column, vocabulary, code in (
                    ("stage", "stage", "row_stage_not_in_vocabulary"),
                    ("evidence_strength", "evidence_strength", "row_evidence_strength_not_in_vocabulary"),
                ):
                    value = fields.get(column, "")
                    if value and value not in vocab[vocabulary]:
                        violations.append(
                            Violation(
                                DATASET_FILE,
                                code,
                                f"{where}: {column} '{value}' is not in {VOCABULARIES_FILE}",
                            )
                        )
            if fields.get("id") and fields.get("class"):
                classes[fields["id"]] = fields["class"]
        return classes


def _sections(body: str) -> dict[str, str]:
    """`## Heading` -> the text up to the next `## ` heading, keyed by the lowercased heading."""
    matches = list(_SECTION_RE.finditer(body))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[match.group(1).strip().lower()] = body[match.end() : stop]
    return sections


def _field(rung: str, label: str) -> str | None:
    """The value of a `- **Label:** value` line (bold and the dash optional), or None if absent."""
    pattern = re.compile(
        rf"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?{label}(?::\*\*|\*\*:|:)[ \t]*(.*?)[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(rung)
    return match.group(1) if match else None


def _check_protections(rel: str, section: str, violations: list[Violation]) -> None:
    rungs = list(_RUNG_RE.finditer(section))
    if not rungs:
        violations.append(
            Violation(rel, "protections_no_rungs", f"{rel}: Protections has no '### <rung>' heading")
        )
    for i, rung in enumerate(rungs):
        name = rung.group(1)
        stop = rungs[i + 1].start() if i + 1 < len(rungs) else len(section)
        text = section[rung.end() : stop]
        for label, code in (
            ("Source", "rung_missing_source"),
            ("Cost", "rung_missing_cost"),
            ("Breaks when", "rung_missing_breaks_when"),
        ):
            if not _field(text, label):
                violations.append(
                    Violation(rel, code, f"{rel}: rung '{name}' has no '{label}' line with a value")
                )
        cost = _field(text, "Cost")
        if cost and re.search(r"\d", cost) and not _DATED_SOURCE_RE.search(cost):
            violations.append(
                Violation(
                    rel,
                    "cost_figure_without_date",
                    f"{rel}: rung '{name}' gives a cost figure without '(as of YYYY-MM, source)'",
                )
            )


def _check_examples(
    rel: str,
    section: str,
    doc_class: str | None,
    cross_cutting: bool,
    classes: dict[str, str] | None,
    violations: list[Violation],
) -> None:
    cited = list(dict.fromkeys(_INCIDENT_ID_RE.findall(section)))
    if not EXAMPLES_MIN <= len(cited) <= EXAMPLES_MAX:
        violations.append(
            Violation(
                rel,
                "examples_count",
                f"{rel}: Examples cite {len(cited)} incidents, need {EXAMPLES_MIN}-{EXAMPLES_MAX}",
            )
        )
    if classes is None:
        return
    for incident in cited:
        if incident not in classes:
            violations.append(
                Violation(
                    rel,
                    f"example_unknown_id:{incident}",
                    f"{rel}: {incident} is not in {DATASET_FILE}",
                )
            )
        elif doc_class and not cross_cutting and classes[incident] != doc_class:
            violations.append(
                Violation(
                    rel,
                    f"example_wrong_class:{incident}",
                    f"{rel}: {incident} is a '{classes[incident]}' row, the doc is '{doc_class}'",
                )
            )


def _check_class_doc(
    root: Path,
    doc_path: Path,
    vocab: dict | None,
    classes: dict[str, str] | None,
    violations: list[Violation],
) -> None:
    rel = _rel(doc_path, root)
    raw = doc_path.read_bytes()
    size = len(raw)  # the cap is on the file as stored, whatever its line endings
    text = raw.decode("utf-8").replace("\r\n", "\n")
    fields = _parse_frontmatter(text)
    if size > DOC_SIZE_CAP_BYTES:
        violations.append(
            Violation(
                rel,
                "doc_over_size_cap",
                f"{rel} is {size} bytes, over the {DOC_SIZE_CAP_BYTES}-byte cap",
            )
        )
    for key in REQUIRED_KEYS:
        if not fields.get(key):
            violations.append(
                Violation(
                    rel,
                    f"doc_missing_key:{key}",
                    f"{rel} is missing required frontmatter key '{key}'",
                )
            )
    surfaces_at = fields.get("surfaces_at")
    if surfaces_at and not isinstance(surfaces_at, list):
        violations.append(
            Violation(rel, "surfaces_at_not_list", f"{rel}: 'surfaces_at' must be a list")
        )
    elif surfaces_at and vocab is not None:
        for value in surfaces_at:
            if value not in vocab["surfaces_at"]:
                violations.append(
                    Violation(
                        rel,
                        f"surfaces_at_not_in_vocabulary:{value}",
                        f"{rel}: surfaces_at '{value}' is not in {VOCABULARIES_FILE}",
                    )
                )
    cross_cutting = False
    if "scope" in fields:
        cross_cutting = fields["scope"] == CROSS_CUTTING
        if not cross_cutting:
            violations.append(
                Violation(rel, "scope_invalid", f"{rel}: 'scope' may only be '{CROSS_CUTTING}'")
            )
    frontmatter_end = text.find("\n---", 4) if text.startswith("---\n") else -1
    sections = _sections(text[frontmatter_end:] if frontmatter_end != -1 else text)
    for name in REQUIRED_SECTIONS:
        if name.lower() not in sections:
            violations.append(
                Violation(rel, f"doc_missing_section:{name}", f"{rel} has no '## {name}' section")
            )
    doc_class = fields.get("class")
    if "examples" in sections:
        _check_examples(
            rel,
            sections["examples"],
            doc_class if isinstance(doc_class, str) else None,
            cross_cutting,
            classes,
            violations,
        )
    if "protections" in sections:
        _check_protections(rel, sections["protections"], violations)
    for link in _MARKDOWN_LINK_RE.findall(text):
        if "://" in link or link.startswith(("#", "mailto:")):
            continue
        if not (doc_path.parent / link).resolve().is_file():
            violations.append(
                Violation(
                    rel,
                    "boundary_evidence_unresolvable",
                    f"{rel} references '{link}', which does not resolve to a file",
                )
            )


def _check_class_docs(
    root: Path,
    definition: dict,
    vocab: dict | None,
    classes: dict[str, str] | None,
    violations: list[Violation],
) -> None:
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return
    for doc_path in sorted(docs_dir.rglob("*.md")):
        if is_hold_path(_rel(doc_path, root), definition):
            _check_class_doc(root, doc_path, vocab, classes, violations)


def check_tree(root: Path) -> list[Violation]:
    root = Path(root)
    violations: list[Violation] = []
    definition = _load_hold_paths(root, violations)
    vocab = _load_vocabularies(root, violations)
    for directory in (CLASS_DOCS_DIR, DATASET_DIR):
        if not (root / directory).is_dir():
            violations.append(
                Violation(
                    directory,
                    "scan_dir_missing",
                    f"scanned directory {directory} does not exist",
                )
            )
    classes = _check_dataset(root, vocab, violations) if (root / DATASET_DIR).is_dir() else None
    if definition is not None:
        _check_class_docs(root, definition, vocab, classes, violations)
    return violations
