from pathlib import Path

from structure_gate import check_tree

FIXTURES = Path(__file__).parent / "fixtures" / "structure_gate"
REPO_ROOT = Path(__file__).parent.parent


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _codes(root: Path) -> set[tuple[str, str]]:
    return {(v.path, v.code) for v in check_tree(root)}


def test_doc_missing_frontmatter_key_fails():
    codes = _codes(FIXTURES / "doc_missing_keys")
    assert (
        "docs/no-applies-when-not.md",
        "doc_missing_key:applies_when_not",
    ) in codes


def test_empty_tree_passes():
    assert check_tree(FIXTURES / "empty") == []


def test_resources_directory_is_not_checked(tmp_path):
    """Only docs/ is scanned (#167): a keyless file with a dangling link elsewhere is not the
    gate's business."""
    _write(tmp_path / "resources" / "bare.md", "# Bare\n\n[gone](gone.md)\n")
    assert check_tree(tmp_path) == []


def test_boundary_evidence_pointer_unresolvable_fails():
    codes = _codes(FIXTURES / "boundary_unresolvable")
    assert ("docs/dangling-evidence.md", "boundary_evidence_unresolvable") in codes


def test_doc_over_size_cap_fails():
    codes = _codes(FIXTURES / "doc_over_size_cap")
    assert ("docs/huge.md", "doc_over_size_cap") in codes


def test_fully_valid_tree_passes():
    assert check_tree(FIXTURES / "valid") == []


def test_excluded_doc_dirs_skip_every_doc_check(tmp_path):
    # A decision record under docs/adr/ has no doc frontmatter and is not a reader-facing doc
    # (#144); the same file anywhere else under docs/ fails as a doc.
    _write(tmp_path / "docs" / "adr" / "0001-x.md", "# ADR-0001\n\nDecided.\n")
    _write(tmp_path / "docs" / "adr" / "nested" / "0002-y.md", "# ADR-0002\n")
    assert check_tree(tmp_path) == []
    _write(tmp_path / "docs" / "other" / "0001-x.md", "# Not an ADR\n\nDecided.\n")
    codes = _codes(tmp_path)
    assert ("docs/other/0001-x.md", "doc_missing_key:applies_when") in codes
    assert not any(path.startswith("docs/adr/") for path, _ in codes)


def test_real_adr_directory_holds_a_first_record():
    assert (REPO_ROOT / "docs" / "adr" / "0001-review-doc-calibration-2.md").is_file()


def test_real_tree_passes_structure_gate():
    assert check_tree(REPO_ROOT) == []


_MIN_DOC = """---
applies_when: fixture
applies_when_not: fixture
---

{body}
"""


def test_relative_link_to_missing_file_is_reported_once(tmp_path):
    _write(tmp_path / "docs" / "a.md", _MIN_DOC.format(body="# A\n\n[x](nope.md)"))
    assert [(v.path, v.code) for v in check_tree(tmp_path)] == [
        ("docs/a.md", "boundary_evidence_unresolvable")
    ]


def test_external_anchor_and_mailto_links_are_not_resolved_as_files(tmp_path):
    body = "# A\n\n[x](https://example.com/p#frag) [m](mailto:a@b.c) [t](#a)"
    _write(tmp_path / "docs" / "a.md", _MIN_DOC.format(body=body))
    assert check_tree(tmp_path) == []
