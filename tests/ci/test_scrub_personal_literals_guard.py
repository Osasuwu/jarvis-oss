"""Meta-test for the personal-literal scrub step in .github/workflows/gitleaks.yml (#23).

Reimplements scripts/scrub_personal_literals.py's decision rule and pins the
workflow shape it depends on:

  - the literal list comes only from ``secrets.PERSONAL_LITERALS`` (newline-
    separated), never from a file in the tree (D25,
    Osasuwu/jarvis docs/decisions/2026-Q3.md)
  - a hit anywhere in the tree fails the step
  - job output never contains the literal value itself, only the file path
  - the step lives in the same workflow file as gitleaks (AC4)

Not path-filtered on ``pull_request`` (scans the whole tree unconditionally,
same posture as gitleaks itself), so it is out of scope for the
paths-filtered-guard convention enforced by test_guard_test_convention.py —
this meta-test exists because the decision rule is non-trivial, not because
the mechanical convention requires it.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from scrub_personal_literals import find_literal_hits, parse_literals, run

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "gitleaks.yml"


# --- parse_literals ---------------------------------------------------------


def test_parse_literals_splits_newlines():
    assert parse_literals("alice@example.com\nSecretStreet123") == [
        "alice@example.com",
        "SecretStreet123",
    ]


def test_parse_literals_strips_blank_lines():
    assert parse_literals("a\n\n  \nb\n") == ["a", "b"]


def test_parse_literals_empty_string_returns_empty_list():
    assert parse_literals("") == []
    assert parse_literals("   \n  \n") == []


# --- find_literal_hits -------------------------------------------------------


def test_find_literal_hits_detects_file_containing_literal(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("contact: alice@example.com\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com"], tmp_path)
    assert hits == ["notes.txt"]


def test_find_literal_hits_no_match_returns_empty(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("nothing sensitive here\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com"], tmp_path)
    assert hits == []


def test_find_literal_hits_excludes_git_dir(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "COMMIT_EDITMSG").write_text("alice@example.com\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com"], tmp_path)
    assert hits == []


def test_find_literal_hits_multiple_literals_dedupes_file_once(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alice@example.com / 555-0100\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com", "555-0100"], tmp_path)
    assert hits == ["notes.txt"]


def test_find_literal_hits_reports_multiple_files_sorted(tmp_path: Path):
    (tmp_path / "b.txt").write_text("alice@example.com\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("alice@example.com\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com"], tmp_path)
    assert hits == ["a.txt", "b.txt"]


def test_find_literal_hits_matches_on_any_single_literal_not_all(tmp_path: Path):
    """A file needs only ONE of several literals to be flagged, not all of them."""
    (tmp_path / "notes.txt").write_text("alice@example.com only, nothing else\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com", "555-0100"], tmp_path)
    assert hits == ["notes.txt"]


def test_find_literal_hits_empty_literal_list_never_matches(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("anything at all\n", encoding="utf-8")
    assert find_literal_hits([], tmp_path) == []


# --- run() — output must never contain the literal value (AC3) --------------


def test_run_reports_path_but_never_the_literal_value(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alice@example.com\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = run(["alice@example.com"], tmp_path)
    output = buf.getvalue()
    assert exit_code == 1
    assert "notes.txt" in output
    assert "alice@example.com" not in output


def test_run_clean_tree_exits_zero(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = run(["alice@example.com"], tmp_path)
    assert exit_code == 0


def test_run_empty_literal_list_skips_and_exits_zero(tmp_path: Path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = run([], tmp_path)
    assert exit_code == 0


# --- workflow shape (config half) -------------------------------------------


def test_workflow_defines_scrub_job_in_gitleaks_file():
    """AC4: the scrub step lives in the same workflow set (file) as gitleaks."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scrub" in text.lower(), (
        "gitleaks.yml has no scrub job/step; #23 AC4 requires the personal-literal "
        "scrub to run in the same workflow set as gitleaks."
    )


def test_workflow_reads_literal_list_from_secret_not_a_file():
    """AC2: the list lives only in a repository secret, never in the tree."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "secrets.PERSONAL_LITERALS" in text, (
        "gitleaks.yml must source the literal list from secrets.PERSONAL_LITERALS, "
        "not a file in the tree."
    )


def test_workflow_scrub_step_unfiltered_on_pull_request():
    """AC1: runs on every PR — mirrors gitleaks' own unconditional pull_request trigger."""
    import yaml

    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    on = doc.get(True) or doc.get("on") or {}
    pr_trigger = on.get("pull_request")
    assert pr_trigger is None or "paths" not in pr_trigger, (
        "pull_request trigger must stay unfiltered for the scrub step to run on every PR."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
