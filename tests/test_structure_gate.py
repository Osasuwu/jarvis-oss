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
