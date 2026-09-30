import subprocess
from pathlib import Path

import pytest

from structure_gate import _SIGNOFF_ENTRY_RE, check_tree

FIXTURES = Path(__file__).parent / "fixtures" / "structure_gate"
REPO_ROOT = Path(__file__).parent.parent


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_DOC_BODY = """---
applies_when: fixture doc for signoff git-history tests
applies_when_not: not applicable outside this fixture
signed_off: 2026-09-16
---

# Git-history fixture doc
"""


def test_doc_missing_frontmatter_key_fails():
    violations = check_tree(FIXTURES / "doc_missing_keys")
    codes = {(v.path, v.code) for v in violations}
    assert (
        "docs/no-applies-when-not.md",
        "doc_missing_key:applies_when_not",
    ) in codes


def test_empty_tree_passes():
    assert check_tree(FIXTURES / "empty") == []


def test_example_missing_fit_or_provenance_fails():
    missing_fit = check_tree(FIXTURES / "example_missing_fit")
    codes = {(v.path, v.code) for v in missing_fit}
    assert ("examples/own-setup.md", "example_missing_key:fit") in codes

    missing_provenance = check_tree(FIXTURES / "example_missing_provenance")
    codes = {(v.path, v.code) for v in missing_provenance}
    assert ("examples/orphan.md", "example_missing_provenance") in codes


def test_resources_directory_is_not_checked(tmp_path):
    """A `resources` directory is not a doc kind (#167): a file there, keyless or dangling, is not the
    gate's business, so the removed rules cannot come back through it."""
    _write(tmp_path / "resources" / "bare.md", "---\npairs_with: docs/gone.md\n---\n\n# Bare\n")
    assert check_tree(tmp_path) == []


def test_pairs_with_unresolvable_fails():
    violations = check_tree(FIXTURES / "pairs_with_unresolvable")
    codes = {(v.path, v.code) for v in violations}
    assert ("examples/dangling.md", "pairs_with_unresolvable") in codes


def test_example_stale_last_seen_fails():
    violations = check_tree(FIXTURES / "example_stale_last_seen")
    codes = {(v.path, v.code) for v in violations}
    assert ("examples/old.md", "example_last_seen_stale") in codes


def test_boundary_evidence_pointer_unresolvable_fails():
    violations = check_tree(FIXTURES / "boundary_unresolvable")
    codes = {(v.path, v.code) for v in violations}
    assert ("docs/dangling-evidence.md", "boundary_evidence_unresolvable") in codes


def test_doc_over_size_cap_fails():
    violations = check_tree(FIXTURES / "doc_over_size_cap")
    codes = {(v.path, v.code) for v in violations}
    assert ("docs/huge.md", "doc_over_size_cap") in codes


def test_fully_valid_tree_passes():
    assert check_tree(FIXTURES / "valid") == []


def test_signoff_missing_ledger_entry_fails():
    violations = check_tree(FIXTURES / "signoff_missing_entry")
    codes = {(v.path, v.code) for v in violations}
    assert ("docs/undocumented.md", "signoff_missing_entry") in codes


def test_signoff_entry_in_same_commit_as_doc_fails(tmp_path):
    _init_repo(tmp_path)
    _write(tmp_path / "docs" / "guide.md", _DOC_BODY)
    _write(tmp_path / "docs" / "SIGNOFF.md", "- `docs/guide.md`: 2026-09-16; facts: human\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add doc and ledger entry together")

    violations = check_tree(tmp_path)
    codes = {(v.path, v.code) for v in violations}
    assert ("docs/guide.md", "signoff_same_commit") in codes


def test_empty_signed_off_is_drafted_not_yet_signed_and_not_a_violation():
    # #53: `signed_off:` present but empty is the intentional "drafted, not yet signed"
    # state, not a violation and not the same as omitting the key. Pinned here so a future
    # edit to `_check_signoff` can't turn this into a `signoff_missing_entry` regression
    # without a test noticing.
    assert check_tree(FIXTURES / "signoff_empty_value") == []


def test_signoff_entry_in_separate_commit_passes(tmp_path):
    _init_repo(tmp_path)
    _write(tmp_path / "docs" / "guide.md", _DOC_BODY)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add doc body")

    _write(tmp_path / "docs" / "SIGNOFF.md", "- `docs/guide.md`: 2026-09-16; facts: human\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add ledger entry")

    violations = check_tree(tmp_path)
    assert violations == []


def test_signoff_entry_without_facts_fails(tmp_path):
    # #59: a signature must say who checked facts and completeness, so an entry that names
    # only a date is a violation even when it is otherwise valid.
    _init_repo(tmp_path)
    _write(tmp_path / "docs" / "guide.md", _DOC_BODY)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add doc body")
    _write(tmp_path / "docs" / "SIGNOFF.md", "- `docs/guide.md`: 2026-09-16\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add ledger entry")

    codes = {(v.path, v.code) for v in check_tree(tmp_path)}
    assert ("docs/guide.md", "signoff_missing_facts") in codes


def test_signoff_entry_with_report_url_passes(tmp_path):
    _init_repo(tmp_path)
    _write(tmp_path / "docs" / "guide.md", _DOC_BODY)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add doc body")
    _write(
        tmp_path / "docs" / "SIGNOFF.md",
        "- `docs/guide.md`: 2026-09-16; facts: https://github.com/o/r/pull/1#issuecomment-1\n",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add ledger entry")

    assert check_tree(tmp_path) == []


def test_real_signoff_ledger_exists_with_documented_entry_format():
    ledger_path = REPO_ROOT / "docs" / "SIGNOFF.md"
    assert ledger_path.is_file(), "docs/SIGNOFF.md must exist"
    text = ledger_path.read_text(encoding="utf-8")
    assert (
        "- `<repo-relative path to doc>`: <signed_off date, YYYY-MM-DD>; facts: <human | report URL>"
        in text
    ), "docs/SIGNOFF.md must document the `- `<path>`: <date>; facts: <who>` entry format"
    # An empty Entries section is a valid state, not a broken ledger: nothing has been signed
    # yet, or every signature was withdrawn (#41 — all three were agent self-signatures in
    # unreviewed PRs). This test previously required at least one live entry, which made
    # "no doc is signed" indistinguishable from "the ledger is malformed". What must hold is
    # that whatever entries are listed use the documented format.
    entries_body = text.split("## Entries", 1)[1]
    for line in entries_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            assert _SIGNOFF_ENTRY_RE.match(stripped), f"malformed ledger entry: {stripped}"


def test_excluded_doc_dirs_skip_every_doc_check(tmp_path):
    # A decision record under docs/adr/ has no doc frontmatter and is not a reader-facing doc
    # (#144); the same file anywhere else under docs/ fails as a doc.
    _write(tmp_path / "docs" / "adr" / "0001-x.md", "# ADR-0001\n\nDecided.\n")
    _write(tmp_path / "docs" / "adr" / "nested" / "0002-y.md", "# ADR-0002\n")
    _write(tmp_path / "docs" / "SIGNOFF.md", "# Sign-off ledger\n")
    assert check_tree(tmp_path) == []
    _write(tmp_path / "docs" / "other" / "0001-x.md", "# Not an ADR\n\nDecided.\n")
    codes = {(v.path, v.code) for v in check_tree(tmp_path)}
    assert ("docs/other/0001-x.md", "doc_missing_key:applies_when") in codes
    assert not any(path.startswith("docs/adr/") for path, _ in codes)


def test_real_adr_directory_holds_a_first_record():
    assert (REPO_ROOT / "docs" / "adr" / "0001-review-doc-calibration-2.md").is_file()


def test_real_tree_passes_structure_gate():
    assert check_tree(REPO_ROOT) == []


def test_ci_workflow_fetches_enough_history_for_signoff_check():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "structure-gate.yml"
    text = workflow_path.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text, (
        "structure-gate.yml must fetch full history (fetch-depth: 0) so the "
        "signoff same-commit check can compare commits"
    )


_MIN_DOC = """---
applies_when: fixture
applies_when_not: fixture
signed_off:
---

# Fixture doc
"""


def _example_paired(pairs_with_lines: str) -> str:
    return f"---\nfit: fixture\nlast_seen: 2026-09-16\n{pairs_with_lines}---\n\n# Paired\n"


def test_example_pairs_with_accepts_several_comma_separated_docs(tmp_path):
    """One example can evidence more than one doc (#41): every listed target must resolve."""
    _write(tmp_path / "docs" / "a.md", _MIN_DOC)
    _write(tmp_path / "docs" / "b.md", _MIN_DOC)
    _write(
        tmp_path / "examples" / "shared.md",
        _example_paired("pairs_with: docs/a.md, docs/b.md\n"),
    )
    assert check_tree(tmp_path) == []


@pytest.mark.parametrize(
    "value",
    [
        "docs/gone.md, docs/a.md, docs/b.md",
        "docs/a.md, docs/gone.md, docs/b.md",
        "docs/a.md, docs/b.md, docs/gone.md",
    ],
    ids=["first", "middle", "last"],
)
def test_example_pairs_with_fails_when_any_one_of_several_targets_is_missing(tmp_path, value):
    """Each entry is resolved: a check that skips the first, a middle or the last one passes
    the case where the bad entry sits elsewhere, so all three positions are pinned (#84)."""
    _write(tmp_path / "docs" / "a.md", _MIN_DOC)
    _write(tmp_path / "docs" / "b.md", _MIN_DOC)
    _write(tmp_path / "examples" / "shared.md", _example_paired(f"pairs_with: {value}\n"))
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [
        ("examples/shared.md", "pairs_with_unresolvable")
    ]
    assert "docs/gone.md" in violations[0].message
    assert "docs/a.md" not in violations[0].message
    assert "docs/b.md" not in violations[0].message


@pytest.mark.parametrize(
    "lines",
    [
        "pairs_with:\n",
        "pairs_with:   \n",
        "pairs_with: ,\n",
        "pairs_with:\n  - docs/a.md\n",
    ],
    ids=["empty", "blank", "commas-only", "yaml-block-list"],
)
def test_example_empty_or_list_form_pairs_with_fails(tmp_path, lines):
    """The frontmatter parser reads flat `key: value` lines, so a block list comes out as the
    key with an empty value. Present-but-empty must not pass as a pairing (#84)."""
    _write(tmp_path / "docs" / "a.md", _MIN_DOC)
    _write(tmp_path / "examples" / "loose.md", _example_paired(lines))
    assert ("examples/loose.md", "pairs_with_empty") in _codes(tmp_path)


def test_real_examples_pair_with_docs_only():
    """F3 (#167): every example in this repo evidences a doc under docs/, nothing else."""
    from structure_gate import _parse_frontmatter

    examples = sorted((REPO_ROOT / "examples").glob("*.md"))
    assert examples
    for example in examples:
        fields = _parse_frontmatter(example.read_text(encoding="utf-8"))
        targets = [t.strip() for t in fields["pairs_with"].split(",") if t.strip()]
        assert targets, example.name
        assert all(t.startswith("docs/") for t in targets), example.name


def test_example_missing_pairs_with_fails(tmp_path):
    """An example must say which doc it evidences (#41), the same as a resource."""
    _write(
        tmp_path / "examples" / "loose.md",
        "---\nfit: fixture\nlast_seen: 2026-09-16\n---\n\n# Loose example\n",
    )
    codes = {(v.path, v.code) for v in check_tree(tmp_path)}
    assert ("examples/loose.md", "example_missing_key:pairs_with") in codes


def test_example_pairs_with_must_resolve(tmp_path):
    _write(tmp_path / "docs" / "a.md", _MIN_DOC)
    _write(
        tmp_path / "examples" / "paired.md",
        "---\nfit: fixture\nlast_seen: 2026-09-16\npairs_with: docs/a.md, docs/gone.md\n---\n\n# Paired\n",
    )
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [
        ("examples/paired.md", "pairs_with_unresolvable")
    ]


# --- #160: kind / requires / hub. The rules below apply only to docs that declare `kind:`. ---


def _kind_doc(kind: str | None = "basics", extra: str = "", body: str = "# Doc\n") -> str:
    kind_line = "" if kind is None else f"kind: {kind}\n"
    return (
        "---\napplies_when: fixture\napplies_when_not: fixture\nsigned_off:\n"
        f"{kind_line}{extra}---\n\n{body}"
    )


def _codes(root: Path) -> set[tuple[str, str]]:
    return {(v.path, v.code) for v in check_tree(root)}


# --- #162: option sections of a `kind: practice` doc. ---


def _option(
    n: int = 1,
    title: str = "Do the thing",
    check: str | None = "1. Run it.",
    relations: str | None = "Relations: none",
    heading: str | None = None,
) -> str:
    """One option section; `None` for check / relations leaves that part out."""
    parts = [heading if heading is not None else f"### Option {n}. {title}", "", "Prose.", ""]
    if relations is not None:
        parts += [relations, ""]
    if check is not None:
        parts += ["#### Check", "", check, ""]
    return "\n".join(parts)


def _practice_body(*sections: str) -> str:
    return "# Doc\n\n## The options\n\n" + "\n".join(sections)


def _practice(*sections: str, extra: str = "") -> str:
    return _kind_doc("practice", extra=extra, body=_practice_body(*sections))


def test_kind_outside_practice_hub_basics_fails(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("guide"))
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "doc_kind_invalid")]
    assert "guide" in violations[0].message


def test_kind_practice_hub_basics_pass(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice", body=_practice_body(_option())))
    _write(tmp_path / "docs" / "b.md", _kind_doc("basics"))
    _write(tmp_path / "docs" / "c.md", _kind_doc("hub", body="# Hub\n\n## Children\n"))
    assert check_tree(tmp_path) == []


def test_empty_kind_value_fails(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc(""))
    assert ("docs/a.md", "doc_kind_invalid") in _codes(tmp_path)


def test_requires_resolving_to_headings_passes(tmp_path):
    _write(
        tmp_path / "docs" / "basics.md",
        _kind_doc("basics", body="# Basics\n\n## CI\n\n## Hooks\n"),
    )
    _write(
        tmp_path / "docs" / "a.md",
        _kind_doc(extra="requires: basics#ci, basics#hooks\n"),
    )
    assert check_tree(tmp_path) == []


def test_requires_anchor_missing_is_named(tmp_path):
    _write(tmp_path / "docs" / "basics.md", _kind_doc("basics", body="# Basics\n\n## CI\n"))
    _write(
        tmp_path / "docs" / "a.md",
        _kind_doc(extra="requires: basics#ci, basics#hooks\n"),
    )
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "requires_unresolvable")]
    assert "basics#hooks" in violations[0].message
    assert "basics#ci" not in violations[0].message


def test_requires_doc_missing_is_named(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc(extra="requires: nowhere#ci\n"))
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "requires_unresolvable")]
    assert "nowhere#ci" in violations[0].message


def test_requires_item_without_anchor_form_fails(tmp_path):
    _write(tmp_path / "docs" / "basics.md", _kind_doc("basics", body="# Basics\n\n## CI\n"))
    for bad in ("basics", "basics#", "#ci", "docs/basics#ci"):
        _write(tmp_path / "docs" / "a.md", _kind_doc(extra=f"requires: {bad}\n"))
        violations = check_tree(tmp_path)
        assert [(v.path, v.code) for v in violations] == [("docs/a.md", "requires_unresolvable")], (
            bad
        )


def test_requires_ignored_on_a_doc_without_kind(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc(kind=None, extra="requires: nowhere#ci\n"))
    assert check_tree(tmp_path) == []


def test_requires_anchor_follows_the_github_heading_slug_rule(tmp_path):
    body = (
        "# Title\n\n"
        "## Чем проверять: хуки\n\n"
        "## Hooks & `pre-push` (v2)\n\n"
        "## [Linked](#setup) *heading* _one_\n\n"
        "## Setup\n\n## Setup\n\n"
        "## Closed heading ##\n\n"
        "```\n## Not a heading\n```\n"
    )
    _write(tmp_path / "docs" / "basics.md", _kind_doc("basics", body=body))
    good = (
        "basics#чем-проверять-хуки, basics#hooks--pre-push-v2, basics#linked-heading-one, "
        "basics#setup, basics#setup-1, basics#closed-heading, basics#title"
    )
    _write(tmp_path / "docs" / "a.md", _kind_doc(extra=f"requires: {good}\n"))
    assert check_tree(tmp_path) == []
    _write(tmp_path / "docs" / "a.md", _kind_doc(extra="requires: basics#not-a-heading\n"))
    assert ("docs/a.md", "requires_unresolvable") in _codes(tmp_path)
    _write(tmp_path / "docs" / "a.md", _kind_doc(extra="requires: basics#setup-2\n"))
    assert ("docs/a.md", "requires_unresolvable") in _codes(tmp_path)


def test_in_doc_anchor_link_must_resolve(tmp_path):
    body = "# Doc\n\n## Setup\n\nSee [setup](#setup) and [Настройка](#%D0%BD%D0%B0%D1%81%D1%82%D1%80%D0%BE%D0%B9%D0%BA%D0%B0).\n\n## Настройка\n"
    _write(tmp_path / "docs" / "a.md", _kind_doc(body=body))
    assert check_tree(tmp_path) == []
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# Doc\n\nSee [gone](#gone).\n"))
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "anchor_unresolvable")]
    assert "#gone" in violations[0].message


def test_relative_link_anchor_must_resolve(tmp_path):
    _write(tmp_path / "docs" / "b.md", _kind_doc(body="# B\n\n## Hooks\n"))
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# A\n\n[hooks](b.md#hooks)\n"))
    assert check_tree(tmp_path) == []
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# A\n\n[hooks](b.md#gone)\n"))
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "anchor_unresolvable")]
    assert "b.md#gone" in violations[0].message


def test_relative_link_anchor_reaches_outside_docs(tmp_path):
    _write(
        tmp_path / "examples" / "e.md",
        "---\nfit: x\nlast_seen: 2999-01-01\npairs_with: docs/a.md\n---\n\n# E\n\n## Run\n",
    )
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# A\n\n[e](../examples/e.md#run)\n"))
    assert check_tree(tmp_path) == []
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# A\n\n[e](../examples/e.md#gone)\n"))
    assert ("docs/a.md", "anchor_unresolvable") in _codes(tmp_path)


def test_relative_link_to_missing_file_is_reported_once(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# A\n\n[x](nope.md#any)\n"))
    assert [(v.path, v.code) for v in check_tree(tmp_path)] == [
        ("docs/a.md", "boundary_evidence_unresolvable")
    ]


def test_external_and_mailto_links_are_not_anchor_checked(tmp_path):
    body = "# A\n\n[x](https://example.com/p#frag) (checked 2026-09-01) [m](mailto:a@b.c)\n"
    _write(tmp_path / "docs" / "a.md", _kind_doc(body=body))
    assert check_tree(tmp_path) == []


def test_doc_without_kind_keeps_todays_link_check(tmp_path):
    # Until the flip (#178) a doc with no `kind` gets exactly the old checks: a `file.md#anchor`
    # link is still reported, and an in-doc `(#anchor)` link is still skipped, never resolved.
    _write(tmp_path / "docs" / "b.md", _kind_doc(kind=None, body="# B\n\n## Hooks\n"))
    _write(
        tmp_path / "docs" / "a.md",
        _kind_doc(kind=None, body="# A\n\n[h](b.md#hooks) [g](#gone)\n"),
    )
    assert [(v.path, v.code) for v in check_tree(tmp_path)] == [
        ("docs/a.md", "boundary_evidence_unresolvable")
    ]


def test_fragment_on_a_non_markdown_target_is_not_a_heading_anchor(tmp_path):
    # `script.py#L10` is a GitHub line anchor, not a heading: only markdown targets are resolved.
    _write(tmp_path / "docs" / "run.py", "print('x')\n")
    _write(tmp_path / "docs" / "a.md", _kind_doc(body="# A\n\n[code](run.py#L1)\n"))
    assert check_tree(tmp_path) == []


def _hub(children: list[str], extra_section: str = "") -> str:
    bullets = "".join(f"- [{name}]({name}.md) — child\n" for name in children)
    return _kind_doc("hub", body=f"# Hub\n\n## Children\n\n{bullets}{extra_section}")


def _child(hub: str = "h", kind: str | None = "basics") -> str:
    return _kind_doc(kind, extra=f"hub: {hub}\n")


def test_hub_listing_exactly_its_children_passes(tmp_path):
    _write(tmp_path / "docs" / "h.md", _hub(["a", "b"]))
    _write(tmp_path / "docs" / "a.md", _child())
    _write(tmp_path / "docs" / "b.md", _child())
    _write(tmp_path / "docs" / "other.md", _kind_doc())  # no `hub:`: not a child
    assert check_tree(tmp_path) == []


def test_hub_children_may_be_listed_in_any_order_with_anchors(tmp_path):
    body = "# Hub\n\n## Children\n\n- [b](b.md#x)\n* [a](a.md)\n\n## Next\n\nprose\n"
    _write(tmp_path / "docs" / "h.md", _kind_doc("hub", body=body))
    _write(tmp_path / "docs" / "a.md", _child())
    _write(
        tmp_path / "docs" / "b.md", _kind_doc("basics", extra="hub: h\n", body="# B\n\n## X\n")
    )
    assert check_tree(tmp_path) == []


def test_hub_target_must_have_kind_hub(tmp_path):
    _write(tmp_path / "docs" / "h.md", _kind_doc("basics"))
    _write(tmp_path / "docs" / "a.md", _child())
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "hub_target_not_hub")]
    assert "h" in violations[0].message


def test_hub_target_without_kind_or_missing_fails(tmp_path):
    _write(tmp_path / "docs" / "plain.md", _kind_doc(kind=None))
    _write(tmp_path / "docs" / "a.md", _child(hub="plain"))
    _write(tmp_path / "docs" / "b.md", _child(hub="nowhere"))
    _write(tmp_path / "docs" / "c.md", _child(hub=""))
    codes = _codes(tmp_path)
    assert {
        ("docs/a.md", "hub_target_not_hub"),
        ("docs/b.md", "hub_target_not_hub"),
        ("docs/c.md", "hub_target_not_hub"),
    } <= codes


def test_hub_missing_a_child_names_it(tmp_path):
    _write(tmp_path / "docs" / "h.md", _hub(["a"]))
    _write(tmp_path / "docs" / "a.md", _child())
    _write(tmp_path / "docs" / "b.md", _child())
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/h.md", "hub_children_mismatch")]
    assert "docs/b.md" in violations[0].message
    assert "docs/a.md" not in violations[0].message


def test_hub_listing_an_extra_doc_names_it(tmp_path):
    _write(tmp_path / "docs" / "h.md", _hub(["a", "stray"]))
    _write(tmp_path / "docs" / "a.md", _child())
    _write(tmp_path / "docs" / "stray.md", _kind_doc())  # exists, but does not declare hub: h
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/h.md", "hub_children_mismatch")]
    assert "docs/stray.md" in violations[0].message
    assert "docs/a.md" not in violations[0].message


def test_hub_names_missing_and_extra_together(tmp_path):
    _write(tmp_path / "docs" / "h.md", _hub(["stray"]))
    _write(tmp_path / "docs" / "stray.md", _kind_doc())
    _write(tmp_path / "docs" / "a.md", _child())
    (violation,) = check_tree(tmp_path)
    assert violation.code == "hub_children_mismatch"
    assert "missing docs/a.md" in violation.message
    assert "extra docs/stray.md" in violation.message


def test_hub_without_children_section_fails(tmp_path):
    _write(tmp_path / "docs" / "h.md", _kind_doc("hub", body="# Hub\n\nNo section.\n"))
    assert _codes(tmp_path) == {("docs/h.md", "hub_children_missing")}


def test_hub_children_section_must_be_a_bullet_list_of_links(tmp_path):
    _write(tmp_path / "docs" / "a.md", _child())
    for bad in ("a.md is the only child\n", "- a\n", "- [a](a.md)\nfree prose\n", "1. [a](a.md)\n"):
        body = f"# Hub\n\n## Children\n\n{bad}"
        _write(tmp_path / "docs" / "h.md", _kind_doc("hub", body=body))
        assert ("docs/h.md", "hub_children_malformed") in _codes(tmp_path), bad


def test_hub_child_declared_on_a_doc_without_kind_is_not_counted(tmp_path):
    _write(tmp_path / "docs" / "h.md", _hub([]))
    _write(tmp_path / "docs" / "a.md", _kind_doc(kind=None, extra="hub: h\n"))
    assert check_tree(tmp_path) == []


def test_hub_child_bullet_may_continue_on_an_indented_line(tmp_path):
    body = "# Hub\n\n## Children\n\n- [a](a.md) — first line\n  continued here\n"
    _write(tmp_path / "docs" / "h.md", _kind_doc("hub", body=body))
    _write(tmp_path / "docs" / "a.md", _child())
    assert check_tree(tmp_path) == []


def test_practice_doc_with_well_formed_options_passes(tmp_path):
    _write(tmp_path / "docs" / "a.md", _practice(_option(1), _option(2, "Other way")))
    assert check_tree(tmp_path) == []


def test_practice_doc_without_the_options_section_fails(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice", body="# Doc\n\n## Notes\n"))
    assert _codes(tmp_path) == {("docs/a.md", "options_section_missing")}


def test_the_options_heading_inside_a_code_fence_is_not_the_section(tmp_path):
    body = "# Doc\n\n```\n## The options\n\n### Option 1. x\n```\n"
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice", body=body))
    assert _codes(tmp_path) == {("docs/a.md", "options_section_missing")}


def test_the_options_section_needs_at_least_one_option(tmp_path):
    body = "# Doc\n\n## The options\n\n### Across all of them\n\nShared advice.\n"
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice", body=body))
    assert _codes(tmp_path) == {("docs/a.md", "options_none")}


def test_other_h3_headings_under_the_options_are_not_options(tmp_path):
    # No `#### Check` and no `Relations:` on the shared section: it is not an option.
    body = _practice_body(_option(1), "### Across all of them\n\nShared advice.\n")
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice", body=body))
    assert check_tree(tmp_path) == []


def test_option_heading_that_misses_the_fixed_prefix_fails(tmp_path):
    for bad in (
        "### Option 2 — Title",
        "### Option 2: Title",
        "### Option two. Title",
        "### Option 2.Title",
        "### Option 3.",
        "### option 1. Title",
    ):
        _write(tmp_path / "docs" / "a.md", _practice(_option(1), _option(heading=bad)))
        violations = check_tree(tmp_path)
        assert [(v.path, v.code) for v in violations] == [("docs/a.md", "option_heading_malformed")], bad
        assert bad.removeprefix("### ") in violations[0].message


def test_h3_outside_the_options_section_is_not_an_option(tmp_path):
    body = _practice_body(_option(1)) + "\n## Later\n\n### Option 9. Not an option here\n"
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice", body=body))
    assert check_tree(tmp_path) == []


def test_option_rules_do_not_apply_to_other_kinds_or_no_kind(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("basics", body="# A\n\n## The options\n"))
    _write(tmp_path / "docs" / "b.md", _kind_doc(None, body="# B\n\n## The options\n"))
    assert check_tree(tmp_path) == []


# --- #162: the `#### Check` subsection of each option. ---


def _practice_codes(tmp_path: Path, *sections: str) -> set[str]:
    _write(tmp_path / "docs" / "a.md", _practice(*sections))
    return {code for path, code in _codes(tmp_path) if path == "docs/a.md"}


def test_option_check_with_steps_passes(tmp_path):
    assert _practice_codes(tmp_path, _option(check="1. Run it.\n2. Read the output.")) == set()


def test_option_check_missing_fails(tmp_path):
    assert _practice_codes(tmp_path, _option(check=None)) == {"option_check_missing"}


def test_option_check_empty_fails(tmp_path):
    assert _practice_codes(tmp_path, _option(check="")) == {"option_check_empty"}


def test_option_check_none_with_reason_passes(tmp_path):
    assert _practice_codes(tmp_path, _option(check="None — nothing to verify here.")) == set()


def test_option_check_none_needs_the_em_dash_and_a_reason(tmp_path):
    for body in ("None", "None - hyphen instead", "None —", "None —  "):
        assert _practice_codes(tmp_path, _option(check=body)) == {"option_check_none_form"}, body


def test_option_check_none_must_be_a_single_line(tmp_path):
    body = "None — because.\n\n1. And also a step."
    assert _practice_codes(tmp_path, _option(check=body)) == {"option_check_none_form"}


def test_option_check_heading_inside_a_code_fence_is_not_the_subsection(tmp_path):
    fenced = "### Option 1. Do the thing\n\nRelations: none\n\n```\n#### Check\n\n1. Run it.\n```\n"
    assert _practice_codes(tmp_path, fenced) == {"option_check_missing"}


def test_option_check_belongs_to_its_own_option(tmp_path):
    codes = _practice_codes(tmp_path, _option(1, check=None), _option(2))
    assert codes == {"option_check_missing"}


def test_option_check_is_not_required_of_non_option_h3(tmp_path):
    other = "### Background\n\nJust prose.\n"
    assert _practice_codes(tmp_path, _option(), other) == set()


def test_option_check_body_stops_at_the_next_heading(tmp_path):
    body = "1. Run it.\n\n#### Notes\n\nMore."
    assert _practice_codes(tmp_path, _option(check=body)) == set()
    empty_then_heading = "\n#### Notes\n\nMore."
    assert _practice_codes(tmp_path, _option(check=empty_then_heading)) == {"option_check_empty"}


def test_option_check_rules_do_not_apply_without_practice_kind(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("basics", body="### Option 1. X\n\nno check\n"))
    assert _codes(tmp_path) == set()


def test_option_with_another_h4_but_no_check_fails(tmp_path):
    other = "### Option 1. Do the thing\n\nRelations: none\n\n#### Notes\n\n1. A step-like line.\n"
    assert _practice_codes(tmp_path, other) == {"option_check_missing"}


# --- #162: the `Relations:` line of each option. ---

_TWO_OPTIONS_SLUGS = ("#option-1-do-the-thing", "#option-2-do-the-thing")


def _related(relations: str, n: int = 1) -> str:
    return _option(n, relations=relations)


def test_option_relations_none_passes(tmp_path):
    assert _practice_codes(tmp_path, _option(relations="Relations: none")) == set()


def test_option_relations_with_all_three_kinds_and_anchors_pass(tmp_path):
    one, two = _TWO_OPTIONS_SLUGS
    line = f"Relations: needs: [one]({one}); excludes: [two]({two}), [one]({one}); trade-off: [two]({two})"
    assert _practice_codes(tmp_path, _related(line), _option(2)) == set()


def test_option_relations_external_link_is_plain(tmp_path):
    line = "Relations: needs: [The service](https://example.com/svc) (checked 2026-09-01)"
    assert _practice_codes(tmp_path, _related(line)) == set()


def test_option_relations_missing_fails(tmp_path):
    assert _practice_codes(tmp_path, _option(relations=None)) == {"option_relations_missing"}


def test_option_relations_twice_fails(tmp_path):
    two_lines = "Relations: none\n\nRelations: none"
    assert _practice_codes(tmp_path, _related(two_lines)) == {"option_relations_duplicate"}


def test_option_relations_malformed_fails(tmp_path):
    ok = "[a](#option-1-do-the-thing)"
    for line in (
        "Relations:",
        "Relations: maybe",
        "Relations: none, actually",
        "Relations: needs:",
        "Relations: needs: plain words",
        f"Relations: requires: {ok}",
        f"Relations: needs: {ok} and some prose",
        f"Relations: needs: {ok};",
        f"Relations: {ok}",
    ):
        assert _practice_codes(tmp_path, _related(line)) == {"option_relations_malformed"}, line


def test_option_relations_anchor_is_checked_by_the_resolver(tmp_path):
    line = "Relations: needs: [nope](#no-such-option)"
    assert _practice_codes(tmp_path, _related(line)) == {"anchor_unresolvable"}


def test_option_relations_line_inside_a_code_fence_does_not_count(tmp_path):
    fenced = "### Option 1. Do the thing\n\n```\nRelations: none\n```\n\n#### Check\n\n1. Run it.\n"
    assert _practice_codes(tmp_path, fenced) == {"option_relations_missing"}


def test_option_relations_belongs_to_its_own_option(tmp_path):
    codes = _practice_codes(tmp_path, _option(1, relations=None), _option(2))
    assert codes == {"option_relations_missing"}


def test_option_relations_after_check_is_not_check_body(tmp_path):
    body = "Relations: none"
    assert _practice_codes(tmp_path, _option(relations=None, check=body)) == {"option_check_empty"}


def test_option_relations_is_not_required_of_non_option_h3(tmp_path):
    other = "### Background\n\nJust prose.\n"
    assert _practice_codes(tmp_path, _option(), other) == set()


# --- #162: plan names are banned from applies_when / applies_when_not. ---


def _plan_doc(applies_when: str = "fixture", applies_when_not: str = "fixture") -> str:
    return (
        f"---\napplies_when: {applies_when}\napplies_when_not: {applies_when_not}\n"
        "signed_off:\nkind: basics\n---\n\n# Doc\n"
    )


def _plan_codes(tmp_path: Path, text: str, name: str = "a.md") -> set[str]:
    _write(tmp_path / "docs" / name, text)
    return {code for path, code in _codes(tmp_path) if path == f"docs/{name}"}


def test_plan_names_lists_every_plan_of_the_five_vendors():
    from structure_gate import PLAN_NAMES

    assert {
        "Claude Free", "Claude Pro", "Claude Max", "Claude Team", "Claude Enterprise",
        "ChatGPT Free", "ChatGPT Plus", "ChatGPT Pro", "ChatGPT Team", "ChatGPT Enterprise",
        "GitHub Free", "GitHub Pro", "GitHub Team", "GitHub Enterprise",
        "Copilot Free", "Copilot Pro", "Copilot Pro+", "Copilot Business", "Copilot Enterprise",
        "GitLab Free", "GitLab Premium", "GitLab Ultimate",
    } <= set(PLAN_NAMES)  # fmt: skip


def test_plan_name_in_applies_when_fails(tmp_path):
    codes = _plan_codes(tmp_path, _plan_doc(applies_when="you are on Claude Max"))
    assert codes == {"plan_name_in_frontmatter"}


def test_plan_name_in_applies_when_not_fails(tmp_path):
    codes = _plan_codes(tmp_path, _plan_doc(applies_when_not="you run GitLab Ultimate"))
    assert codes == {"plan_name_in_frontmatter"}


def test_plan_name_match_is_case_insensitive_and_whitespace_tolerant(tmp_path):
    assert _plan_codes(tmp_path, _plan_doc(applies_when="on chatgpt   PLUS")) == {
        "plan_name_in_frontmatter"
    }


def test_plan_name_with_a_plus_sign_matches(tmp_path):
    assert _plan_codes(tmp_path, _plan_doc(applies_when="Copilot Pro+ seat")) == {
        "plan_name_in_frontmatter"
    }


def test_plan_name_matches_whole_phrases_only(tmp_path):
    for text in ("Claude Professional", "the GitHub Freedom act", "MyClaude Pro", "Claude Maxim"):
        assert _plan_codes(tmp_path, _plan_doc(applies_when=text)) == set(), text


def test_plan_name_is_allowed_in_the_harnesses_doc(tmp_path):
    text = _plan_doc(applies_when="you are on Claude Max")
    assert _plan_codes(tmp_path, text, name="harnesses.md") == set()


def test_plan_name_is_allowed_outside_the_two_frontmatter_fields(tmp_path):
    text = _plan_doc().replace("# Doc", "# Doc\n\nClaude Max is a plan.")
    assert _plan_codes(tmp_path, text) == set()


def test_plan_name_ban_needs_a_kind(tmp_path):
    text = _plan_doc(applies_when="you are on Claude Max").replace("kind: basics\n", "")
    assert _plan_codes(tmp_path, text) == set()


# --- #162: every external link of a kind doc carries a check date. ---


def _linked(body: str, kind: str | None = "basics") -> str:
    return _kind_doc(kind, body=f"# Doc\n\n{body}\n")


def _link_codes(tmp_path: Path, body: str, kind: str | None = "basics") -> set[str]:
    _write(tmp_path / "docs" / "a.md", _linked(body, kind))
    return {code for path, code in _codes(tmp_path) if path == "docs/a.md"}


def test_external_link_with_a_check_date_passes(tmp_path):
    assert _link_codes(tmp_path, "See [x](https://example.com/x) (checked 2026-09-01).") == set()


def test_external_link_with_a_volatile_check_date_passes(tmp_path):
    body = "See [x](https://example.com/x) (checked 2026-09-01, volatile)."
    assert _link_codes(tmp_path, body) == set()


def test_external_link_check_date_of_today_passes(tmp_path):
    from datetime import date

    body = f"[x](https://example.com/x) (checked {date.today().isoformat()})"
    assert _link_codes(tmp_path, body) == set()


def test_external_link_without_a_check_date_fails(tmp_path):
    codes = _link_codes(tmp_path, "See [x](https://example.com/x) for more.")
    assert codes == {"external_link_date_missing"}


def test_external_link_at_the_end_of_the_line_without_a_date_fails(tmp_path):
    assert _link_codes(tmp_path, "[x](http://example.com/x)") == {"external_link_date_missing"}


def test_external_link_with_a_malformed_check_date_fails(tmp_path):
    for note in (
        "(checked 2026-9-1)",
        "(checked 2026-09-01, stale)",
        "(checked 2026-09-01,volatile)",
        "(checked yesterday)",
        "(checked 2026-13-45)",
        "(checked )",
        "(checked 2026-09-01 volatile)",
    ):
        codes = _link_codes(tmp_path, f"[x](https://example.com/x) {note}")
        assert codes == {"external_link_date_malformed"}, note


def test_external_link_with_a_future_check_date_fails(tmp_path):
    from datetime import date, timedelta

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    codes = _link_codes(tmp_path, f"[x](https://example.com/x) (checked {tomorrow})")
    assert codes == {"external_link_date_future"}


def test_each_external_link_on_a_line_needs_its_own_date(tmp_path):
    body = "[a](https://a.example/) (checked 2026-09-01) and [b](https://b.example/)"
    assert _link_codes(tmp_path, body) == {"external_link_date_missing"}


def test_external_link_date_must_follow_the_link(tmp_path):
    body = "(checked 2026-09-01) [a](https://a.example/)"
    assert _link_codes(tmp_path, body) == {"external_link_date_missing"}


def test_external_link_in_a_code_fence_needs_no_date(tmp_path):
    body = "```\n[x](https://example.com/x)\n```"
    assert _link_codes(tmp_path, body) == set()


def test_internal_and_mailto_links_need_no_date(tmp_path):
    body = "# Top\n\n[a](#top) [b](mailto:me@example.com)"
    assert _link_codes(tmp_path, body) == set()


def test_check_dates_are_not_required_without_a_kind(tmp_path):
    assert _link_codes(tmp_path, "[x](https://example.com/x)", kind=None) == set()


def test_external_link_in_a_relation_needs_a_date_too(tmp_path):
    line = "Relations: needs: [Svc](https://example.com/svc)"
    _write(tmp_path / "docs" / "a.md", _practice(_option(relations=line)))
    codes = {c for p, c in _codes(tmp_path) if p == "docs/a.md"}
    assert codes == {"external_link_date_missing"}
