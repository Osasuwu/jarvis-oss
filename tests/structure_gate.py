# Install and check: docs/doc-structure-gate.md#8-a-custom-script-in-ci
"""Structure gate: validates the docs/examples bucket frontmatter contract.

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
from urllib.parse import unquote

STALE_AFTER_DAYS = 180

SIGNOFF_LEDGER_PATH = "docs/SIGNOFF.md"
# Directories under docs/ that hold records, not reader-facing docs: decision records (#144).
# Mirrored byte for byte in scripts/doc_review.py; tests/test_doc_review.py pins the two equal.
EXCLUDED_DOC_DIRS = ("docs/adr/",)


def is_excluded_doc(path: str) -> bool:
    """The sign-off ledger and every file under an excluded directory: one rule, shared with
    scripts/doc_review.py."""
    return path == SIGNOFF_LEDGER_PATH or path.startswith(EXCLUDED_DOC_DIRS)


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
# #160: a doc that declares `kind:` opts in to the contract checks below; a doc without it keeps
# exactly the checks above until every doc is migrated (#178).
DOC_KINDS = ("practice", "hub", "basics")

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_BULLET_LINK_RE = re.compile(r"^[-*+][ \t]+\[[^\]]*\]\(([^)\s]+)\)")

# #162 D10: the fixed option-heading prefix, which the gate keys on. A heading that starts with the
# word "Option" but misses the form is a malformed option, not a free-form heading, so an option
# cannot drop out of the checks by a typo.
OPTIONS_H2 = (2, "The options")
_OPTION_HEADING_RE = re.compile(r"^Option [0-9]+\. \S")
_OPTION_LIKE_RE = re.compile(r"^option\b", re.IGNORECASE)
CHECK_HEADING = (4, "Check")
_RELATIONS_PREFIX = "Relations:"
_RELATION_CLAUSE_RE = re.compile(r"^(needs|excludes|trade-off):(.*)$")
_ISO_DATE = "[0-9]{4}-[0-9]{2}-[0-9]{2}"
_CHECKED_NOTE_RE = re.compile(r"^\s*\(checked ([^)]*)\)")
_CHECKED_DATE_RE = re.compile(f"({_ISO_DATE})(?:, volatile)?")
_CHECKED_ANNOTATION_RE = re.compile(rf"\(checked {_ISO_DATE}(, volatile)?\)")
_CHECK_NONE_RE = re.compile(r"^None\b")
_CHECK_NONE_FORM_RE = re.compile(r"^None — \S")

# #162: plan names go stale as vendors rename tiers, so `applies_when` / `applies_when_not` state
# the condition (a capability, a setting) instead; `docs/harnesses.md` is the one doc that
# catalogues plans and is exempt.
PLAN_NAMES = (
    "Claude Free", "Claude Pro", "Claude Max", "Claude Team", "Claude Enterprise",
    "ChatGPT Free", "ChatGPT Plus", "ChatGPT Pro", "ChatGPT Team", "ChatGPT Enterprise",
    "GitHub Free", "GitHub Pro", "GitHub Team", "GitHub Enterprise",
    "Copilot Free", "Copilot Pro", "Copilot Pro+", "Copilot Business", "Copilot Enterprise",
    "GitLab Free", "GitLab Premium", "GitLab Ultimate",
)  # fmt: skip
PLAN_NAME_EXEMPT_DOC = "docs/harnesses.md"
_PLAN_NAME_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(r"\s+".join(re.escape(word) for word in name.split()) for name in PLAN_NAMES)
    + r")(?!\w)",
    re.IGNORECASE,
)


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


def _body_after_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    return text if end == -1 else text[end + 4 :]


def _scan_lines(
    text: str, blank_fenced: bool = False
) -> list[tuple[str, tuple[int, str] | None]]:
    """Each body line with its (level, heading text) when it is an ATX heading; lines in
    fenced code, and the fences themselves, are never headings. With `blank_fenced` they are
    also emptied, so a caller matching prose lines never sees code."""
    scanned: list[tuple[str, tuple[int, str] | None]] = []
    fence: str | None = None
    for line in _body_after_frontmatter(text).splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            scanned.append(("" if blank_fenced else line, None))
            continue
        heading_match = _HEADING_RE.match(line) if fence is None else None
        if heading_match and heading_match.group(2):
            scanned.append((line, (len(heading_match.group(1)), heading_match.group(2))))
        else:
            scanned.append(("" if blank_fenced and fence is not None else line, None))
    return scanned


def _headings(text: str) -> list[tuple[int, str]]:
    return [heading for _, heading in _scan_lines(text) if heading is not None]


def _github_slug(heading: str) -> str:
    """GitHub's heading-slug rule: rendered text, lowercased, everything except letters,
    digits, underscore, hyphen and space dropped, each space turned into a hyphen."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"(?<!\w)_+(.+?)_+(?!\w)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def _anchors(text: str) -> set[str]:
    """Every anchor GitHub generates for the doc; a repeated heading gets -1, -2, ..."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for _, heading in _headings(text):
        slug = _github_slug(heading)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _anchors_of(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    resolved = path.resolve()
    if resolved not in cache:
        cache[resolved] = _anchors(resolved.read_text(encoding="utf-8"))
    return cache[resolved]


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
            # A kind doc's `file.md#anchor` link is resolved by _check_kind_docs (#160, #84);
            # a doc without `kind` keeps the old check and reports it.
            file_part = link.partition("#")[0] if "kind" in fields else link
            target = (doc_path.parent / file_part).resolve()
            if not target.is_file():
                violations.append(
                    Violation(
                        path=rel,
                        code="boundary_evidence_unresolvable",
                        message=f"{rel} references '{link}', which does not resolve to a file",
                    )
                )
    return violations


def _check_requires(
    root: Path, rel: str, value: str, cache: dict[Path, set[str]]
) -> list[Violation]:
    # #160 D7: a flat comma-separated list of `<doc-stem>#<anchor>`; each names a heading of
    # docs/<doc-stem>.md.
    violations: list[Violation] = []
    for item in (part.strip() for part in value.split(",")):
        if not item:
            continue
        stem, sep, anchor = item.partition("#")
        target = root / "docs" / f"{stem}.md"
        resolves = (
            bool(sep and stem and anchor)
            and "/" not in stem
            and "\\" not in stem
            and target.is_file()
            and anchor in _anchors_of(target, cache)
        )
        if not resolves:
            violations.append(
                Violation(
                    path=rel,
                    code="requires_unresolvable",
                    message=(
                        f"{rel} requires '{item}', which does not resolve to a heading "
                        "of docs/<doc-stem>.md"
                    ),
                )
            )
    return violations


def _check_anchor_links(
    doc_path: Path, rel: str, text: str, cache: dict[Path, set[str]]
) -> list[Violation]:
    # The one anchor resolver for links: `(#anchor)` in this doc and `other.md#anchor`. A link
    # whose file does not exist is already reported by _check_docs, so it is skipped here.
    violations: list[Violation] = []
    for link in _MARKDOWN_LINK_RE.findall(text):
        if "://" in link or link.startswith("mailto:"):
            continue
        file_part, _, fragment = link.partition("#")
        if not fragment:
            continue
        target = (doc_path.parent / file_part).resolve() if file_part else doc_path
        if not target.is_file() or target.suffix != ".md":
            continue
        if unquote(fragment) not in _anchors_of(target, cache):
            violations.append(
                Violation(
                    path=rel,
                    code="anchor_unresolvable",
                    message=f"{rel} links to '{link}', which does not resolve to a heading",
                )
            )
    return violations


def _children_links(text: str) -> tuple[list[str], list[str]] | None:
    """(links, malformed lines) of the `## Children` section, or None when there is none."""
    links: list[str] = []
    malformed: list[str] = []
    in_section = False
    found = False
    for line, heading in _scan_lines(text):
        if heading is not None:
            if in_section and heading[0] <= 2:
                break
            if heading == (2, "Children"):
                in_section = found = True
            continue
        if not in_section or not line.strip() or line[0] in " \t":
            continue
        match = _BULLET_LINK_RE.match(line)
        if match:
            links.append(match.group(1))
        else:
            malformed.append(line.strip())
    return (links, malformed) if found else None


def _check_hub_target(hub_rel: str, rel: str, kinds: dict[str, str]) -> list[Violation]:
    if hub_rel in kinds and kinds[hub_rel] == "hub":
        return []
    reason = "does not exist" if hub_rel not in kinds else f"has kind '{kinds[hub_rel]}'"
    return [
        Violation(
            path=rel,
            code="hub_target_not_hub",
            message=f"{rel} declares hub '{hub_rel}', which {reason}, not kind: hub",
        )
    ]


def _check_hub_children(
    root: Path, doc_path: Path, rel: str, text: str, children: set[str]
) -> list[Violation]:
    section = _children_links(text)
    if section is None:
        return [
            Violation(
                path=rel,
                code="hub_children_missing",
                message=f"{rel} is kind: hub and has no '## Children' section",
            )
        ]
    links, malformed = section
    violations = [
        Violation(
            path=rel,
            code="hub_children_malformed",
            message=f"{rel} '## Children' holds a line that is not a bullet link: '{line}'",
        )
        for line in malformed
    ]
    listed: set[str] = set()
    for link in links:
        target = (doc_path.parent / link.partition("#")[0]).resolve()
        try:
            listed.add(target.relative_to(root.resolve()).as_posix())
        except ValueError:
            listed.add(link)
    missing, extra = sorted(children - listed), sorted(listed - children)
    if missing or extra:
        parts = [f"missing {', '.join(missing)}"] if missing else []
        parts += [f"extra {', '.join(extra)}"] if extra else []
        violations.append(
            Violation(
                path=rel,
                code="hub_children_mismatch",
                message=(
                    f"{rel} '## Children' does not list exactly the docs that declare "
                    f"hub '{doc_path.stem}': {'; '.join(parts)}"
                ),
            )
        )
    return violations


_ScannedLine = tuple[str, tuple[int, str] | None]


def _option_sections(text: str) -> list[tuple[str, list[_ScannedLine]]] | None:
    """(heading text, scanned body lines) of each H3 under `## The options`, or None when the
    doc has no such H2. A section runs to the next heading of level 3 or above."""
    sections: list[tuple[str, list[_ScannedLine]]] = []
    in_options = found = False
    for line, heading in _scan_lines(text, blank_fenced=True):
        if heading is not None and heading[0] <= 2:
            if in_options:
                break
            in_options = found = heading == OPTIONS_H2
            continue
        if not in_options:
            continue
        if heading is not None and heading[0] == 3:
            sections.append((heading[1], []))
        elif sections:
            sections[-1][1].append((line, heading))
    return sections if found else None


def _check_option_check(rel: str, title: str, lines: list[_ScannedLine]) -> list[Violation]:
    """AC2: the option has a `#### Check` whose body is steps or one `None — <reason>` line."""
    body: list[str] = []
    found = in_check = False
    for line, heading in lines:
        if heading is not None:
            if in_check:
                break
            in_check = found = heading == CHECK_HEADING
        elif in_check and line.strip() and not line.startswith(_RELATIONS_PREFIX):
            body.append(line.strip())
    if not found:
        code, what = "option_check_missing", "has no '#### Check' subsection"
    elif not body:
        code, what = "option_check_empty", "has an empty '#### Check' subsection"
    elif _CHECK_NONE_RE.match(body[0]) and not (
        len(body) == 1 and _CHECK_NONE_FORM_RE.match(body[0])
    ):
        code = "option_check_none_form"
        what = "'#### Check' says None; the form is one line 'None — <reason>'"
    else:
        return []
    return [Violation(path=rel, code=code, message=f"{rel} option '{title}' {what}")]


def _relations_clause_ok(clause: str) -> bool:
    match = _RELATION_CLAUSE_RE.match(clause.strip())
    if not match or not _MARKDOWN_LINK_RE.search(match.group(2)):
        return False
    rest = _CHECKED_ANNOTATION_RE.sub("", _MARKDOWN_LINK_RE.sub("", match.group(2)))
    return not rest.replace(",", "").strip()


def _check_option_relations(rel: str, title: str, lines: list[_ScannedLine]) -> list[Violation]:
    """AC3: one `Relations:` line, `none` or `needs:`/`excludes:`/`trade-off:` clauses of links."""
    found = [line for line, _ in lines if line.startswith(_RELATIONS_PREFIX)]
    if not found:
        code, what = "option_relations_missing", "has no 'Relations:' line"
    elif len(found) > 1:
        code, what = "option_relations_duplicate", "has more than one 'Relations:' line"
    else:
        value = found[0][len(_RELATIONS_PREFIX) :].strip()
        if value == "none" or all(_relations_clause_ok(c) for c in value.split(";")):
            return []
        code = "option_relations_malformed"
        what = (
            f"has the malformed line '{found[0].strip()}'; the form is 'Relations: none' or "
            "'needs:' / 'excludes:' / 'trade-off:' clauses of links, joined by ';'"
        )
    return [Violation(path=rel, code=code, message=f"{rel} option '{title}' {what}")]


def _check_options(rel: str, text: str) -> list[Violation]:
    sections = _option_sections(text)
    if sections is None:
        return [
            Violation(
                path=rel,
                code="options_section_missing",
                message=f"{rel} is kind: practice and has no '## The options' section",
            )
        ]
    violations: list[Violation] = []
    options = 0
    for title, lines in sections:
        if _OPTION_HEADING_RE.match(title):
            options += 1
            violations.extend(_check_option_relations(rel, title, lines))
            violations.extend(_check_option_check(rel, title, lines))
        elif _OPTION_LIKE_RE.match(title):
            violations.append(
                Violation(
                    path=rel,
                    code="option_heading_malformed",
                    message=(
                        f"{rel} has the heading '### {title}'; an option heading is "
                        "'### Option <N>. <title>'"
                    ),
                )
            )
    if not options:
        violations.append(
            Violation(
                path=rel,
                code="options_none",
                message=f"{rel} '## The options' holds no '### Option <N>. <title>' section",
            )
        )
    return violations


def _parse_link_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_link_dates(rel: str, text: str) -> list[Violation]:
    """AC5: each external inline link is followed by `(checked YYYY-MM-DD[, volatile])`."""
    violations: list[Violation] = []
    for line, _ in _scan_lines(text, blank_fenced=True):
        for match in _MARKDOWN_LINK_RE.finditer(line):
            if "://" not in match.group(1):
                continue
            note = _CHECKED_NOTE_RE.match(line[match.end() :])
            if note is None:
                code, what = "external_link_date_missing", "has no '(checked YYYY-MM-DD)' after it"
            else:
                form = _CHECKED_DATE_RE.fullmatch(note.group(1))
                checked = _parse_link_date(form.group(1)) if form else None
                if checked is None:
                    code = "external_link_date_malformed"
                    what = f"has the note '(checked {note.group(1)})'; the form is 'YYYY-MM-DD'"
                elif checked > date.today():
                    code, what = "external_link_date_future", f"has the future check date {checked}"
                else:
                    continue
            violations.append(
                Violation(
                    path=rel,
                    code=code,
                    message=f"{rel} external link '{match.group(1)}' {what}",
                )
            )
    return violations


def _check_plan_names(rel: str, fields: dict[str, str]) -> list[Violation]:
    if rel == PLAN_NAME_EXEMPT_DOC:
        return []
    violations: list[Violation] = []
    for key in ("applies_when", "applies_when_not"):
        match = _PLAN_NAME_RE.search(fields.get(key, ""))
        if match:
            violations.append(
                Violation(
                    path=rel,
                    code="plan_name_in_frontmatter",
                    message=(
                        f"{rel} '{key}' names the plan '{match.group(0)}'; state the condition "
                        "(a capability or setting), not a plan name"
                    ),
                )
            )
    return violations


def _check_kind_docs(root: Path) -> list[Violation]:
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return []
    violations: list[Violation] = []
    cache: dict[Path, set[str]] = {}
    declared: list[tuple[Path, str, str, dict[str, str]]] = []
    for doc_path in sorted(docs_dir.rglob("*.md")):
        rel = _rel(doc_path, root)
        if is_excluded_doc(rel):
            continue
        text = doc_path.read_text(encoding="utf-8")
        fields = _parse_frontmatter(text)
        if "kind" in fields:
            declared.append((doc_path, rel, text, fields))
    kinds = {rel: fields["kind"] for _, rel, _, fields in declared}
    children: dict[str, set[str]] = {}
    for doc_path, rel, text, fields in declared:
        if fields["kind"] not in DOC_KINDS:
            violations.append(
                Violation(
                    path=rel,
                    code="doc_kind_invalid",
                    message=(
                        f"{rel} has kind '{fields['kind']}', not one of {', '.join(DOC_KINDS)}"
                    ),
                )
            )
        violations.extend(_check_requires(root, rel, fields.get("requires", ""), cache))
        violations.extend(_check_anchor_links(doc_path, rel, text, cache))
        violations.extend(_check_plan_names(rel, fields))
        violations.extend(_check_link_dates(rel, text))
        if fields["kind"] == "practice":
            violations.extend(_check_options(rel, text))
        if "hub" in fields:
            hub_rel = f"docs/{fields['hub']}.md"
            violations.extend(_check_hub_target(hub_rel, rel, kinds))
            children.setdefault(hub_rel, set()).add(rel)
    for doc_path, rel, text, fields in declared:
        if fields["kind"] == "hub":
            violations.extend(
                _check_hub_children(root, doc_path, rel, text, children.get(rel, set()))
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
        if is_excluded_doc(rel):
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
    if "pairs_with" in fields and not targets:
        # The parser reads flat `key: value` lines, so a YAML block list also lands here (#84).
        return [
            Violation(
                path=rel,
                code="pairs_with_empty",
                message=f"{rel} pairs_with names no doc; the form is 'pairs_with: docs/<doc>.md'",
            )
        ]
    return [
        Violation(
            path=rel,
            code="pairs_with_unresolvable",
            message=f"{rel} pairs_with '{target}' does not resolve to a file",
        )
        for target in targets
        if not (root / target).is_file()
    ]


def check_tree(root: Path) -> list[Violation]:
    root = Path(root)
    violations: list[Violation] = []
    violations.extend(_check_docs(root))
    violations.extend(_check_kind_docs(root))
    violations.extend(_check_signoff(root))
    violations.extend(_check_examples(root))
    return violations
