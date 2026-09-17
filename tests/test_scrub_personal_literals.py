"""Tests for the personal-literal scrub CI step (#23, D25).

Schema source: Osasuwu/jarvis docs/decisions/2026-Q3.md (D25). The literal list
lives only in the PERSONAL_LITERALS repository secret, never in the tree; a
hit anywhere in the tree fails the check; job output never contains the
matched literal, only the file path; the step runs in the same workflow set
as gitleaks.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from scrub_personal_literals import find_literal_hits, main, parse_literals, run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "gitleaks.yml"


# --- AC1: a hit anywhere in the tree fails the check ------------------------


def test_find_literal_hits_detects_file_containing_literal(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("contact: alice@example.com\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com"], tmp_path)
    assert hits == ["notes.txt"]


def test_find_literal_hits_no_match_returns_empty(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("nothing sensitive here\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com"], tmp_path)
    assert hits == []


def test_run_fails_when_literal_present(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alice@example.com\n", encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        exit_code = run(["alice@example.com"], tmp_path)
    assert exit_code == 1


def test_find_literal_hits_matches_on_any_single_literal_not_all(tmp_path: Path):
    """A file needs only ONE of several literals to be flagged, not all of them."""
    (tmp_path / "notes.txt").write_text("alice@example.com only, nothing else\n", encoding="utf-8")
    hits = find_literal_hits(["alice@example.com", "555-0100"], tmp_path)
    assert hits == ["notes.txt"]


def test_run_clean_tree_exits_zero(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        exit_code = run(["alice@example.com"], tmp_path)
    assert exit_code == 0


def test_parse_literals_splits_on_newlines_and_skips_blank_lines():
    assert parse_literals("alice@example.com\n\n  555-0100  \n") == ["alice@example.com", "555-0100"]


# --- AC2: literal list lives only in the PERSONAL_LITERALS secret/env, never in the tree ----


def test_main_sources_literals_only_from_personal_literals_env_var(tmp_path, monkeypatch):
    """main() must wire the PERSONAL_LITERALS env var (the secret, at CI time) into the scan —
    not any file checked into the tree."""
    (tmp_path / "notes.txt").write_text("alice@example.com\n", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_LITERALS", "alice@example.com")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    with redirect_stdout(io.StringIO()):
        exit_code = main()
    assert exit_code == 1


def test_main_with_no_personal_literals_env_var_fails_closed(tmp_path, monkeypatch):
    """No secret set → nothing was checked, so the step must fail, not report clean (#41).

    The first version passed here, and the repository secret was never set: every PR's scrub
    printed "Scrub clean" while checking zero literals."""
    (tmp_path / "notes.txt").write_text("alice@example.com\n", encoding="utf-8")
    monkeypatch.delenv("PERSONAL_LITERALS", raising=False)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main()
    assert exit_code == 1
    assert "PERSONAL_LITERALS" in buf.getvalue()


def test_blank_only_personal_literals_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_LITERALS", "  \n\n ")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    with redirect_stdout(io.StringIO()):
        exit_code = main()
    assert exit_code == 1


def test_clean_run_reports_how_many_literals_it_checked(tmp_path):
    """A clean pass must show it checked something, without printing any literal."""
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        run(["alice@example.com", "555-0100"], tmp_path)
    output = buf.getvalue()
    assert "2 literal" in output
    assert "alice@example.com" not in output


# --- AC3: job output never contains the matched literal, only the file path ------------------


def test_run_output_contains_path_but_never_the_literal_value(tmp_path):
    (tmp_path / "notes.txt").write_text("alice@example.com\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        run(["alice@example.com"], tmp_path)
    output = buf.getvalue()
    assert "notes.txt" in output
    assert "alice@example.com" not in output


# --- AC4: gitleaks runs in the same workflow set as the scrub step ----------------------------


def test_gitleaks_workflow_exists_and_wires_secret_and_scrub_step():
    assert WORKFLOW_PATH.exists(), f"expected {WORKFLOW_PATH} to exist"
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "secrets.PERSONAL_LITERALS" in content
    assert "scripts/scrub_personal_literals.py" in content
    assert "gitleaks" in content.lower()


def test_gitleaks_workflow_triggers_on_pull_request():
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pull_request:" in content
