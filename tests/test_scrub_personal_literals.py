"""Tests for the personal-literal scrub CI step (#23, #84).

The contract: the literal list lives only in the PERSONAL_LITERALS repository
secret, never in the tree, and its blank and `#` lines are not literals; a hit
anywhere in the tree, in a file's content or in its path, fails the check; the
job log is public, so neither stdout nor stderr ever contains a literal, and a
path that carries one is shown redacted; the step runs in the same workflow
set as gitleaks.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest  # noqa: E402
from scrub_personal_literals import (  # noqa: E402
    compile_literal_matcher,
    find_literal_hits,
    main,
    parse_literals,
    run,
)

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


def test_parse_literals_skips_comment_lines():
    raw = "# who is on the list\nalice@example.com\n  # indented comment\n555-0100\n#\n"
    assert parse_literals(raw) == ["alice@example.com", "555-0100"]


def test_comment_line_in_the_list_is_not_scanned_for(tmp_path, monkeypatch):
    """A `# ...` line is a note to the list's author, not a literal (#84): text that matches
    the comment must not fail the scrub, and the count names only the real entries."""
    (tmp_path / "notes.md").write_text("# Owner list\n\nNobody yet.\n", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_LITERALS", "# owner list\nalice@example.com\n")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main()
    assert exit_code == 0
    assert "checked 1 literal(s)" in buf.getvalue()


def test_list_of_only_comments_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_LITERALS", "# no entries yet\n\n# still none\n")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main()
    assert exit_code == 1
    assert "No personal literals configured" in buf.getvalue()


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


# --- #100 AC1: variants of each known literal ----------------------------------------------
#
# Every literal here is fake. The matcher treats case, the separators `-`, `_`, space, `.`,
# `/`, `\`, `:` (or none at all) and camelCase boundaries as the same literal, which also
# covers the usual path forms of a path literal.

FAKE_NAME = "Zorblax Quint"
FAKE_PATH = r"C:\Users\zqfake\vault"


@pytest.mark.parametrize(
    "text",
    [
        "Zorblax Quint",  # exact
        "zorblax quint",  # lower case
        "ZORBLAX QUINT",  # upper case
        "zOrBlAx qUiNt",  # mixed case
        "zorblax-quint",  # hyphen
        "zorblax_quint",  # underscore
        "zorblax  quint",  # two spaces
        "zorblaxquint",  # no separator
        "ZorblaxQuint",  # camelCase, no separator
        "see zorblax.quint for details",  # dot, inside a sentence
        "zorblax\tquint",  # tab
    ],
)
def test_matcher_catches_case_and_separator_variants(text):
    assert compile_literal_matcher([FAKE_NAME]).search(text)


@pytest.mark.parametrize(
    "text",
    [
        r"C:\Users\zqfake\vault",  # as listed
        "C:/Users/zqfake/vault",  # forward slashes
        r"c:\users\zqfake\vault",  # lower-case drive and dirs
        "/c/Users/zqfake/vault",  # Git Bash / MSYS form
        r"C:\\Users\\zqfake\\vault",  # JSON-escaped backslashes
        "cd /C/USERS/ZQFAKE/VAULT && ls",  # inside a command, upper case
    ],
)
def test_matcher_catches_path_forms_of_a_path_literal(text):
    assert compile_literal_matcher([FAKE_PATH]).search(text)


def test_matcher_catches_literal_listed_in_camel_case_written_with_separators():
    assert compile_literal_matcher(["ZorblaxQuint"]).search("ZORBLAX-QUINT")


@pytest.mark.parametrize(
    "text",
    [
        "Zorblax Quinn",  # different word
        "Zorblax",  # only part of the literal
        "Zorblax, Quint",  # a comma is not a separator
        "Zorblax Quint".replace(" ", " and "),  # other words in between
    ],
)
def test_matcher_does_not_match_non_variants(text):
    assert not compile_literal_matcher([FAKE_NAME]).search(text)


def test_matcher_keeps_non_separator_punctuation_literal():
    matcher = compile_literal_matcher(["zq@fake.example"])
    assert matcher.search("mail ZQ@FAKE-EXAMPLE")
    assert not matcher.search("zq fake example")


def test_matcher_ignores_entries_with_no_letters_or_digits():
    """An entry like `---` would otherwise compile to a pattern that matches everything."""
    assert compile_literal_matcher(["---", "  "]) is None
    matcher = compile_literal_matcher(["---", FAKE_NAME])
    assert not matcher.search("a line with --- in it")
    assert matcher.search("zorblax_quint")


def test_matcher_is_none_for_empty_list():
    assert compile_literal_matcher([]) is None


def test_ci_scrub_find_literal_hits_uses_variants(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("owner: zorblax-quint\n", encoding="utf-8")
    assert find_literal_hits([FAKE_NAME], tmp_path) == ["notes.txt"]


def test_literal_only_in_a_file_name_fails_the_scrub(tmp_path: Path):
    """The content is clean; only the name carries the literal (#84)."""
    (tmp_path / "zorblax-quint-notes.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    assert find_literal_hits([FAKE_NAME], tmp_path) == ["zorblax-quint-notes.txt"]
    with redirect_stdout(io.StringIO()):
        assert run([FAKE_NAME], tmp_path) == 1


def test_literal_only_in_a_directory_name_fails_the_scrub(tmp_path: Path):
    (tmp_path / "ZorblaxQuint").mkdir()
    (tmp_path / "ZorblaxQuint" / "readme.txt").write_text("nothing here\n", encoding="utf-8")
    assert find_literal_hits([FAKE_NAME], tmp_path) == ["ZorblaxQuint/readme.txt"]


SCRIPT_PATH = REPO_ROOT / "scripts" / "scrub_personal_literals.py"


def _run_script(literals: str, root: Path) -> subprocess.CompletedProcess[str]:
    """Run the scrub as CI does, so stdout and stderr are both what the public log shows."""
    env = {k: v for k, v in os.environ.items() if k not in ("PERSONAL_LITERALS", "GITHUB_WORKSPACE")}
    env.update({"PERSONAL_LITERALS": literals, "GITHUB_WORKSPACE": str(root), "PYTHONUTF8": "1"})
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_job_log_never_contains_a_literal_from_a_path(tmp_path: Path):
    """The CI log is public: a hit names the file with the literal redacted, on stdout and
    stderr alike (#84)."""
    (tmp_path / "zorblax-quint-notes.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "ZorblaxQuint").mkdir()
    (tmp_path / "ZorblaxQuint" / "readme.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("owner: Zorblax Quint\n", encoding="utf-8")
    result = _run_script(FAKE_NAME, tmp_path)
    assert result.returncode == 1
    listed = sorted(line.strip() for line in result.stdout.splitlines() if line.startswith("  "))
    assert listed == ["[redacted]-notes.txt", "[redacted]/readme.txt", "notes.txt"]
    for stream in (result.stdout, result.stderr):
        assert "zorblax" not in stream.lower()
        assert "quint" not in stream.lower()


def test_path_that_still_matches_after_redaction_is_shown_as_a_hash(tmp_path: Path):
    """A literal that the redaction marker itself spells must not come back through it."""
    (tmp_path / "redacted.txt").write_text("nothing here\n", encoding="utf-8")
    result = _run_script("redacted", tmp_path)
    assert result.returncode == 1
    listed = [line.strip() for line in result.stdout.splitlines() if line.startswith("  ")]
    assert listed == ["sha256:dbf7c16d95ef (path withheld)"]
    for stream in (result.stdout, result.stderr):
        assert "redacted" not in stream.lower()


def test_ci_scrub_fails_closed_when_no_entry_is_matchable(tmp_path: Path):
    """A list of only punctuation checks nothing, exactly like an empty one (#41)."""
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        assert run(["---", "***"], tmp_path) == 1
