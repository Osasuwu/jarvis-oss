"""Tests for the local pre-push leak gate (#100).

The gate runs as git's `pre-push` hook: it reads the literal list from the same
`PERSONAL_LITERALS` variable the CI scrub reads, scans the commits being pushed (added
content, file names and commit messages) for any variant of a literal, and blocks the push
on a hit without printing the literal. With no list it blocks and says so.

Every literal in this file is fake.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "pre_push_leak_gate.py"
ZERO = "0" * 40

FAKE_LITERAL = "Zorblax Quint"
# Variants of FAKE_LITERAL, as they would land in a file, a message or a path.
FAKE_VARIANTS = ("zorblax-quint", "ZORBLAX_QUINT", "ZorblaxQuint", "zorblax quint")

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A work repo with one clean commit already pushed to a bare `origin`."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    hooks = tmp_path / "no-hooks"
    hooks.mkdir()
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@invalid.example"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(hooks)),
        ("core.autocrlf", "false"),
    ):
        _git(work, "config", key, value)
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README.md").write_text("clean start\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "initial")
    _git(work, "push", "-q", "origin", "main")
    return work


def _commit(repo: Path, files: dict[str, str | None], message: str = "change") -> str:
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _env(literals: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PERSONAL_LITERALS", None)
    if literals is not None:
        env["PERSONAL_LITERALS"] = literals
    return env


def _run_gate(
    repo: Path,
    stdin: str,
    literals: str | None = FAKE_LITERAL,
    remote: str = "origin",
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), remote, "remote-url-unused"],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(literals),
    )


def _push_line(repo: Path, branch: str = "main") -> str:
    local = _git(repo, "rev-parse", "HEAD")
    try:
        remote = _git(repo, "rev-parse", f"origin/{branch}")
    except subprocess.CalledProcessError:
        remote = ZERO
    return f"refs/heads/{branch} {local} refs/heads/{branch} {remote}\n"


def _assert_no_literal_in(output: str) -> None:
    lowered = output.lower()
    assert FAKE_LITERAL.lower() not in lowered
    for variant in FAKE_VARIANTS:
        assert variant.lower() not in lowered


# --- blocks on a hit, never printing the literal ------------------------------------------


@pytest.mark.parametrize("variant", FAKE_VARIANTS)
def test_variant_in_added_content_blocks_the_push(repo: Path, variant: str):
    _commit(repo, {"notes.md": f"owner is {variant}\n"})
    result = _run_gate(repo, _push_line(repo))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "notes.md" in output
    _assert_no_literal_in(output)


def test_literal_only_in_commit_message_blocks_the_push(repo: Path):
    _commit(repo, {"notes.md": "nothing private\n"}, message="fix for zorblax-quint")
    result = _run_gate(repo, _push_line(repo))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "commit message" in output
    _assert_no_literal_in(output)


def test_literal_only_in_a_file_name_blocks_and_withholds_the_name(repo: Path):
    _commit(repo, {"docs/ZorblaxQuint-notes.md": "nothing private\n"})
    result = _run_gate(repo, _push_line(repo))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "file name" in output
    _assert_no_literal_in(output)


def test_content_hit_in_a_file_whose_name_matches_withholds_the_name(repo: Path):
    _commit(repo, {"ZorblaxQuint.md": "written by zorblax quint\n"})
    result = _run_gate(repo, _push_line(repo))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "added content in" in output
    _assert_no_literal_in(output)


def test_literal_added_then_removed_within_the_push_still_blocks(repo: Path):
    """The push carries every commit, so a literal removed by a later commit is still sent."""
    _commit(repo, {"notes.md": "Zorblax Quint\n"})
    _commit(repo, {"notes.md": "scrubbed\n"})
    result = _run_gate(repo, _push_line(repo))
    assert result.returncode != 0


def test_literal_in_the_pushed_branch_name_blocks(repo: Path):
    _git(repo, "checkout", "-q", "-b", "zorblax-quint-fix")
    _commit(repo, {"notes.md": "nothing private\n"})
    result = _run_gate(repo, _push_line(repo, branch="zorblax-quint-fix"))
    assert result.returncode != 0
    _assert_no_literal_in(result.stdout + result.stderr)


def test_literal_in_an_annotated_tag_message_blocks(repo: Path):
    _git(repo, "tag", "-a", "v1", "-m", "release for Zorblax Quint")
    tag = _git(repo, "rev-parse", "v1")
    result = _run_gate(repo, f"refs/tags/v1 {tag} refs/tags/v1 {ZERO}\n")
    assert result.returncode != 0
    _assert_no_literal_in(result.stdout + result.stderr)


# --- passes what it should pass -----------------------------------------------------------


def test_clean_push_passes_and_says_what_it_checked(repo: Path):
    _commit(repo, {"notes.md": "nothing private\n"})
    _commit(repo, {"more.md": "still nothing\n"})
    result = _run_gate(repo, _push_line(repo), literals=f"{FAKE_LITERAL}\nqq-fake-two")
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: clean - checked 2 literal(s) across 2 new commit(s) in 1 pushed "
        "ref(s).\n"
    )


def test_removing_a_literal_that_is_already_public_is_not_blocked(repo: Path):
    """Deleting a leaked line is the fix; blocking it would make the leak unremovable."""
    _commit(repo, {"notes.md": "Zorblax Quint\n"})
    _git(repo, "push", "-q", "origin", "main")
    _commit(repo, {"notes.md": "scrubbed\n"})
    result = _run_gate(repo, _push_line(repo))
    assert result.returncode == 0, result.stderr


def test_push_to_a_url_excludes_what_the_remote_ref_already_has(repo: Path):
    """`git push <url>` passes a URL, not a remote name: the remote sha is the only anchor."""
    _commit(repo, {"notes.md": "Zorblax Quint\n"})
    _git(repo, "push", "-q", "origin", "main")
    _commit(repo, {"notes.md": "scrubbed\n"})
    url = (repo.parent / "remote.git").as_posix()
    result = _run_gate(repo, _push_line(repo), remote=url)
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: clean - checked 1 literal(s) across 1 new commit(s) in 1 pushed "
        "ref(s).\n"
    )


def test_new_branch_scans_only_commits_not_on_the_remote(repo: Path):
    _commit(repo, {"old.md": "Zorblax Quint\n"})
    _git(repo, "push", "-q", "origin", "main")  # already public: not this push's content
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, {"new.md": "nothing private\n"})
    result = _run_gate(repo, _push_line(repo, branch="feature"))
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: clean - checked 1 literal(s) across 1 new commit(s) in 1 pushed "
        "ref(s).\n"
    )


def test_deleting_a_remote_branch_is_not_blocked_and_is_not_called_clean(repo: Path):
    result = _run_gate(repo, f"(delete) {ZERO} refs/heads/old {'a' * 40}\n")
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: nothing checked - this push only deletes 1 remote ref(s), which "
        "sends no content.\n"
    )


def test_push_of_only_public_commits_is_clean_and_says_no_commit_was_new(repo: Path):
    """A new branch at a sha the remote has: lines arrive, 0 commits are new (#273)."""
    head = _git(repo, "rev-parse", "HEAD")
    result = _run_gate(repo, f"refs/heads/main {head} refs/heads/other {ZERO}\n")
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: clean - checked 1 literal(s) across 0 new commit(s) in 1 pushed "
        "ref(s); the remote already has every pushed commit, so only ref and tag names were "
        "checked.\n"
    )


# --- no input, or input it cannot read: never "clean" (#273) ------------------------------


def test_unreadable_push_line_blocks_even_next_to_a_clean_one(repo: Path):
    _commit(repo, {"notes.md": "nothing private\n"})
    result = _run_gate(repo, _push_line(repo) + "refs/heads/main 1234567\n")
    assert result.returncode == 1
    assert result.stderr == (
        "pre-push leak gate: could not read 1 push line(s) (expected <local ref> <local sha> "
        "<remote ref> <remote sha>). Those refs were not checked, so the push is blocked.\n"
    )


NOT_RUN_BY_GIT = (
    "pre-push leak gate: not run by git, which passes <remote> <url>, so stdin was not read. "
    "Nothing was checked. By hand: --range <rev-range>, e.g. --range @{u}..HEAD.\n"
)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], NOT_RUN_BY_GIT),
        (["origin"], NOT_RUN_BY_GIT),
        (
            ["--range"],
            "pre-push leak gate: --range takes exactly one <rev-range>, e.g. --range "
            "@{u}..HEAD. Nothing was checked.\n",
        ),
    ],
)
def test_call_not_shaped_like_git_reads_no_stdin_and_says_nothing_was_checked(
    repo: Path, args: list[str], expected: str
):
    """Stdin stays open and empty: a gate that reads it blocks here until the timeout."""
    proc = subprocess.Popen(
        [sys.executable, str(GATE), *args],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(FAKE_LITERAL),
    )
    try:
        returncode = proc.wait(timeout=30)
    finally:
        proc.kill()
        proc.stdin.close()
    stderr = proc.stderr.read().decode("utf-8").replace("\r\n", "\n")
    proc.stdout.close()
    proc.stderr.close()
    assert returncode == 2
    assert stderr == expected


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("pre_push_leak_gate_under_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _TerminalStdin:
    def isatty(self) -> bool:
        return True

    def read(self, *_args) -> str:
        raise AssertionError("the gate read stdin from a terminal")


def test_hook_call_with_a_terminal_on_stdin_reads_nothing_and_says_so(monkeypatch, capsys):
    gate = _load_gate_module()
    monkeypatch.setattr(sys, "stdin", _TerminalStdin())
    assert gate.main(["pre_push_leak_gate.py", "origin", "remote-url-unused"]) == 2
    assert capsys.readouterr().err == (
        "pre-push leak gate: stdin is a terminal, not git's push lines, so it was not read. "
        "Nothing was checked. By hand: --range <rev-range>, e.g. --range @{u}..HEAD.\n"
    )


# --- manual mode: --range <rev-range> ------------------------------------------------------


def _run_range(repo: Path, rev_range: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--range", rev_range],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(FAKE_LITERAL),
    )


def test_range_blocks_on_a_literal_in_a_commit_of_the_range(repo: Path):
    _commit(repo, {"notes.md": "owner is zorblax-quint\n"})
    result = _run_range(repo, "origin/main..HEAD")
    assert result.returncode == 1
    assert "added content in notes.md" in result.stderr
    _assert_no_literal_in(result.stdout + result.stderr)


def test_range_without_a_literal_is_clean_and_names_the_commit_count(repo: Path):
    _commit(repo, {"notes.md": "nothing private\n"})
    _commit(repo, {"more.md": "still nothing\n"})
    result = _run_range(repo, "origin/main..HEAD")
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: clean - checked 1 literal(s) across 2 commit(s) in range "
        "origin/main..HEAD.\n"
    )


def test_range_name_that_matches_a_literal_is_withheld(repo: Path):
    _git(repo, "checkout", "-q", "-b", "zorblax-quint")
    _commit(repo, {"notes.md": "nothing private\n"})
    result = _run_range(repo, "origin/main..zorblax-quint")
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "pre-push leak gate: clean - checked 1 literal(s) across 1 commit(s) in range "
        "<name withheld: it matches>.\n"
    )


def test_range_with_no_commits_says_nothing_was_checked(repo: Path):
    result = _run_range(repo, "origin/main..HEAD")
    assert result.returncode == 1
    assert result.stderr == (
        "pre-push leak gate: nothing checked - range origin/main..HEAD holds no commits.\n"
    )


def test_range_that_does_not_resolve_says_nothing_was_checked(repo: Path):
    result = _run_range(repo, "no-such-branch..HEAD")
    assert result.returncode == 1
    assert result.stderr.startswith("pre-push leak gate: could not complete the scan (git ")
    assert result.stderr.endswith("). Nothing was checked.\n")


# --- no list: fail closed, and say so -----------------------------------------------------


@pytest.mark.parametrize("literals", [None, "", "  \n\n", "---"])
def test_no_literal_list_blocks_the_push_and_says_so(repo: Path, literals):
    _commit(repo, {"notes.md": "nothing private\n"})
    result = _run_gate(repo, _push_line(repo), literals=literals)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "PERSONAL_LITERALS" in output
    assert "nothing was checked" in output.lower()


# --- end to end: installed as git's pre-push hook -----------------------------------------


def _install_hook(repo: Path) -> None:
    hooks = Path(_git(repo, "config", "core.hooksPath"))
    hook = hooks / "pre-push"
    # The same two lines the install steps give, with this checkout's script path.
    gate = GATE.as_posix()
    hook.write_text(
        f'#!/bin/sh\nexec python3 "{gate}" "$@" || exec python "{gate}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)


def _python3_or_python_on_path() -> bool:
    return shutil.which("python3") is not None or shutil.which("python") is not None


@pytest.mark.skipif(not _python3_or_python_on_path(), reason="no python on PATH for the hook")
def test_installed_hook_blocks_a_real_git_push(repo: Path):
    _install_hook(repo)
    before = _git(repo, "rev-parse", "origin/main")
    _commit(repo, {"notes.md": "nothing private\n"}, message="thanks ZORBLAX_QUINT")
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(FAKE_LITERAL),
    )
    assert result.returncode != 0
    _assert_no_literal_in(result.stdout + result.stderr)
    remote = repo.parent / "remote.git"
    assert _git(remote, "rev-parse", "main") == before


@pytest.mark.skipif(not _python3_or_python_on_path(), reason="no python on PATH for the hook")
def test_installed_hook_lets_a_clean_git_push_through(repo: Path):
    _install_hook(repo)
    head = _commit(repo, {"notes.md": "nothing private\n"})
    result = subprocess.run(
        ["git", "push", "-q", "origin", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(FAKE_LITERAL),
    )
    assert result.returncode == 0, result.stderr
    assert _git(repo.parent / "remote.git", "rev-parse", "main") == head


@pytest.mark.skipif(not _python3_or_python_on_path(), reason="no python on PATH for the hook")
def test_installed_hook_on_an_up_to_date_push_says_nothing_was_checked(repo: Path):
    """Git runs pre-push with no lines when nothing is new; that passes, but is not clean."""
    _install_hook(repo)
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(FAKE_LITERAL),
    )
    assert result.returncode == 0, result.stderr
    gate_lines = [ln for ln in result.stderr.splitlines() if ln.startswith("pre-push leak gate")]
    assert gate_lines == [
        "pre-push leak gate: nothing checked - git sent no ref updates, so there is nothing to "
        "push."
    ]
