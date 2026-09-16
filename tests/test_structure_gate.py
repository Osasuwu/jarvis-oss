from pathlib import Path

from structure_gate import check_tree

FIXTURES = Path(__file__).parent / "fixtures" / "structure_gate"


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
