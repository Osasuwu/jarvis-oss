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
    _write(tmp_path / "docs" / "SIGNOFF.md", "- `docs/guide.md`: 2026-09-16\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add doc and ledger entry together")

    violations = check_tree(tmp_path)
    codes = {(v.path, v.code) for v in violations}
    assert ("docs/guide.md", "signoff_same_commit") in codes


def test_signoff_entry_in_separate_commit_passes(tmp_path):
    _init_repo(tmp_path)
    _write(tmp_path / "docs" / "guide.md", _DOC_BODY)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add doc body")

    _write(tmp_path / "docs" / "SIGNOFF.md", "- `docs/guide.md`: 2026-09-16\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add ledger entry")

    violations = check_tree(tmp_path)
    assert violations == []


def test_real_signoff_ledger_exists_with_documented_entry_format():
    ledger_path = REPO_ROOT / "docs" / "SIGNOFF.md"
    assert ledger_path.is_file(), "docs/SIGNOFF.md must exist"
    text = ledger_path.read_text(encoding="utf-8")
    entry_lines = [line for line in text.splitlines() if _SIGNOFF_ENTRY_RE.match(line.strip())]
    assert entry_lines, "docs/SIGNOFF.md must document the `- `<path>`: <date>` entry format"


def test_real_tree_passes_structure_gate():
    assert check_tree(REPO_ROOT) == []


def test_ci_workflow_fetches_enough_history_for_signoff_check():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "structure-gate.yml"
    text = workflow_path.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text, (
        "structure-gate.yml must fetch full history (fetch-depth: 0) so the "
        "signoff same-commit check can compare commits"
    )
