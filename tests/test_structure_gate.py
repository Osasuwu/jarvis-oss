import subprocess
from pathlib import Path

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


def test_resource_missing_required_keys_fails():
    violations = check_tree(FIXTURES / "resource_missing_keys")
    codes = {(v.path, v.code) for v in violations}
    assert ("resources/bare.md", "resource_missing_key:pairs_with") in codes
    assert ("resources/bare.md", "resource_missing_key:cost") in codes
    assert ("resources/bare.md", "resource_missing_key:harnesses") not in codes


def test_pairs_with_unresolvable_fails():
    violations = check_tree(FIXTURES / "pairs_with_unresolvable")
    codes = {(v.path, v.code) for v in violations}
    assert ("resources/dangling.md", "pairs_with_unresolvable") in codes


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


def test_resource_pairs_with_accepts_several_comma_separated_docs(tmp_path):
    """One resource can serve more than one doc (#41): every listed target must resolve."""
    _write(tmp_path / "docs" / "a.md", _MIN_DOC)
    _write(tmp_path / "docs" / "b.md", _MIN_DOC)
    _write(
        tmp_path / "resources" / "shared.md",
        "---\npairs_with: docs/a.md, docs/b.md\nharnesses: all\ncost: low\n---\n\n# Shared\n",
    )
    assert check_tree(tmp_path) == []


def test_resource_pairs_with_fails_when_any_one_of_several_targets_is_missing(tmp_path):
    _write(tmp_path / "docs" / "a.md", _MIN_DOC)
    _write(
        tmp_path / "resources" / "shared.md",
        "---\npairs_with: docs/a.md, docs/gone.md\nharnesses: all\ncost: low\n---\n\n# Shared\n",
    )
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [
        ("resources/shared.md", "pairs_with_unresolvable")
    ]
    assert "docs/gone.md" in violations[0].message
    assert "docs/a.md" not in violations[0].message


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


def _kind_doc(kind: str | None = "practice", extra: str = "", body: str = "# Doc\n") -> str:
    kind_line = "" if kind is None else f"kind: {kind}\n"
    return (
        "---\napplies_when: fixture\napplies_when_not: fixture\nsigned_off:\n"
        f"{kind_line}{extra}---\n\n{body}"
    )


def _codes(root: Path) -> set[tuple[str, str]]:
    return {(v.path, v.code) for v in check_tree(root)}


def test_kind_outside_practice_hub_basics_fails(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("guide"))
    violations = check_tree(tmp_path)
    assert [(v.path, v.code) for v in violations] == [("docs/a.md", "doc_kind_invalid")]
    assert "guide" in violations[0].message


def test_kind_practice_hub_basics_pass(tmp_path):
    _write(tmp_path / "docs" / "a.md", _kind_doc("practice"))
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
    body = "# A\n\n[x](https://example.com/p#frag) [m](mailto:a@b.c)\n"
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


def _child(hub: str = "h", kind: str | None = "practice") -> str:
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
        tmp_path / "docs" / "b.md", _kind_doc("practice", extra="hub: h\n", body="# B\n\n## X\n")
    )
    assert check_tree(tmp_path) == []


def test_hub_target_must_have_kind_hub(tmp_path):
    _write(tmp_path / "docs" / "h.md", _kind_doc("practice"))
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
