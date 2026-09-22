"""Tests for the local pre-push leak gate (#100).

The gate runs as git's `pre-push` hook: it reads the literal list from the same
`PERSONAL_LITERALS` variable the CI scrub reads, scans the commits being pushed (added
content, file names and commit messages) for any variant of a literal, and blocks the push
on a hit without printing the literal. With no list it blocks and says so.

Every literal in this file is fake.
"""

from __future__ import annotations

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
    output = result.stdout + result.stderr
    assert "2 literal" in output
    assert "2 commit" in output


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
    assert "1 commit" in result.stdout + result.stderr


def test_new_branch_scans_only_commits_not_on_the_remote(repo: Path):
    _commit(repo, {"old.md": "Zorblax Quint\n"})
    _git(repo, "push", "-q", "origin", "main")  # already public: not this push's content
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, {"new.md": "nothing private\n"})
    result = _run_gate(repo, _push_line(repo, branch="feature"))
    assert result.returncode == 0, result.stderr
    assert "1 commit" in result.stdout + result.stderr


def test_deleting_a_remote_branch_is_not_blocked(repo: Path):
    result = _run_gate(repo, f"(delete) {ZERO} refs/heads/old {'a' * 40}\n")
    assert result.returncode == 0, result.stderr


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
