"""Tests for the literal-gate PreToolUse hook (#100 AC3).

Mechanism under test: `.agents/hooks/literal-gate.py`, wired by the matchers in
`.agents/hooks/literal-gate.snippet.json` on `Bash` and on the whole GitHub MCP server
(`^mcp__github__`). It reads the tool call as JSON on stdin and exits 2 to block when the text
about to be sent to GitHub (a PR or issue title/body, a comment, a review, a release note, a
`gh api` write) holds any variant of a personal literal. The hook never prints the literal.

Every literal in this file is fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".agents" / "hooks" / "literal-gate.py"
SNIPPET = ROOT / ".agents" / "hooks" / "literal-gate.snippet.json"

FAKE_LITERAL = "Zorblax Quint"
FAKE_PATH_LITERAL = r"C:\Users\zqfake"
FAKE_VARIANTS = ("zorblax-quint", "ZORBLAX_QUINT", "ZorblaxQuint", "zorblax quint", "zqfake")


def _run(payload, literals: str | None = f"{FAKE_LITERAL}\n{FAKE_PATH_LITERAL}", raw=None):
    env = dict(os.environ)
    env.pop("PERSONAL_LITERALS", None)
    if literals is not None:
        env["PERSONAL_LITERALS"] = literals
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _bash(command: str, cwd: Path | None = None) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return payload


def _mcp(tool: str, **tool_input) -> dict:
    return {"tool_name": f"mcp__github__{tool}", "tool_input": tool_input}


def _assert_blocked_quietly(result) -> None:
    assert result.returncode == 2, (result.returncode, result.stderr)
    output = (result.stdout + result.stderr).lower()
    assert FAKE_LITERAL.lower() not in output
    for variant in FAKE_VARIANTS:
        assert variant.lower() not in output


# --- GitHub MCP: every string field, at any depth -----------------------------------------


@pytest.mark.parametrize(
    "tool, tool_input",
    [
        ("create_pull_request", {"title": "fix", "body": "thanks to zorblax-quint"}),
        ("issue_write", {"method": "create", "title": "ZorblaxQuint report", "body": "x"}),
        ("add_issue_comment", {"issue_number": 1, "body": "cc ZORBLAX_QUINT"}),
        ("merge_pull_request", {"pullNumber": 1, "commit_message": "zorblax quint"}),
        ("push_files", {"files": [{"path": "a.md", "content": "Zorblax.Quint"}]}),
        ("create_or_update_file", {"path": "a.md", "content": "see /c/Users/zqfake/notes"}),
    ],
)
def test_mcp_write_with_a_literal_variant_is_blocked(tool, tool_input):
    _assert_blocked_quietly(_run(_mcp(tool, **tool_input)))


def test_mcp_block_names_the_field_not_the_value():
    result = _run(_mcp("create_pull_request", title="fix", body="thanks zorblax-quint"))
    _assert_blocked_quietly(result)
    assert "body" in result.stderr


def test_mcp_block_withholds_a_field_name_that_itself_matches():
    result = _run(_mcp("issue_write", fields={"ZorblaxQuint": "zorblax quint"}))
    _assert_blocked_quietly(result)
    assert "withheld" in result.stderr


def test_clean_mcp_write_is_allowed():
    result = _run(_mcp("create_pull_request", title="fix", body="nothing private here"))
    assert result.returncode == 0, result.stderr


# --- Bash: gh commands that send text -----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title "fix" --body "thanks zorblax-quint"',
        "gh issue create -t 'ZorblaxQuint' -b 'x'",
        'gh pr comment 5 --body "ZORBLAX_QUINT"',
        'gh issue comment 5 -b "zorblax quint"',
        'gh pr review 5 --comment -b "zorblax quint"',
        'gh pr edit 5 --body "zorblax quint"',
        'gh release create v1 --notes "zorblax quint"',
        "gh api repos/o/r/issues -f title=x -f body='zorblax quint'",
        "gh -R o/r pr create --title x --body 'zorblax quint'",
        "cd /tmp && gh pr create --title x --body 'zorblax quint'",
        "gh pr create --title x --body \"$(cat <<'EOF'\nsummary\nzorblax quint\nEOF\n)\"",
    ],
)
def test_gh_write_command_with_a_literal_variant_is_blocked(command):
    _assert_blocked_quietly(_run(_bash(command)))


def test_body_file_contents_are_scanned(tmp_path: Path):
    body = tmp_path / "body.md"
    body.write_text("## Summary\nthanks zorblax quint\n", encoding="utf-8")
    result = _run(_bash(f'gh pr create --title x --body-file "{body.as_posix()}"'))
    _assert_blocked_quietly(result)
    assert "body file" in result.stderr


def test_body_file_short_flag_and_relative_path_resolve_against_cwd(tmp_path: Path):
    (tmp_path / "body.md").write_text("ZORBLAX_QUINT\n", encoding="utf-8")
    result = _run(_bash("gh issue create -t x -F body.md", cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "body file" in result.stderr


def test_body_file_relative_to_a_cd_in_the_same_command(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "body.md").write_text("zorblax quint\n", encoding="utf-8")
    command = f'cd "{sub.as_posix()}" && gh pr comment 3 --body-file body.md'
    result = _run(_bash(command, cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "body file" in result.stderr


def test_gh_api_field_from_file_is_scanned(tmp_path: Path):
    (tmp_path / "b.md").write_text("zorblax quint\n", encoding="utf-8")
    command = "gh api repos/o/r/issues/1/comments -F body=@b.md"
    result = _run(_bash(command, cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "body file" in result.stderr


def test_clean_body_file_is_allowed_even_when_its_path_matches(tmp_path: Path):
    """The path to a body file is not sent; only its contents are. A scratch directory under
    a private home path must not block a clean body."""
    private_dir = tmp_path / "zqfake"
    private_dir.mkdir()
    body = private_dir / "body.md"
    body.write_text("nothing private\n", encoding="utf-8")
    result = _run(
        _bash(f'gh pr create --title x --body-file "{body.as_posix()}"'),
        literals="zqfake",
    )
    assert result.returncode == 0, result.stderr


def test_block_on_a_body_file_withholds_a_path_that_itself_matches(tmp_path: Path):
    private_dir = tmp_path / "zorblax-quint"
    private_dir.mkdir()
    body = private_dir / "body.md"
    body.write_text("thanks zorblax quint\n", encoding="utf-8")
    result = _run(_bash(f'gh pr create --title x --body-file "{body.as_posix()}"'))
    _assert_blocked_quietly(result)
    assert "body file <path withheld" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive paths exist only on Windows")
def test_body_file_given_as_a_git_bash_drive_path_is_read(tmp_path: Path):
    body = tmp_path / "body.md"
    body.write_text("zorblax quint\n", encoding="utf-8")
    posix = body.resolve().as_posix()  # C:/Users/...
    msys = f"/{posix[0].lower()}{posix[2:]}"  # /c/Users/...
    result = _run(_bash(f'gh pr create --title x --body-file "{msys}"'))
    _assert_blocked_quietly(result)
    assert "could not read" not in result.stderr.lower()
    assert "body file" in result.stderr


def test_leading_cd_into_a_private_path_does_not_block_a_clean_command():
    command = "cd /c/Users/zqfake/repo && gh pr create --title fix --body clean"
    result = _run(_bash(command))
    assert result.returncode == 0, result.stderr


def test_unreadable_body_file_is_blocked(tmp_path: Path):
    missing = tmp_path / "missing.md"
    result = _run(_bash(f'gh pr create --title x --body-file "{missing.as_posix()}"'))
    assert result.returncode == 2
    assert "could not read" in result.stderr.lower()


def test_body_from_a_pipe_is_blocked_because_it_cannot_be_seen():
    result = _run(_bash("cat notes.md | gh pr create --title x --body-file -"))
    assert result.returncode == 2


def test_clean_gh_write_is_allowed():
    result = _run(_bash('gh pr create --title "fix" --body "nothing private"'))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "ls /c/Users/zqfake",  # not a gh write: nothing is sent
        "gh pr view 5",
        "gh issue list --search 'zorblax quint'",
        "gh api repos/o/r/pulls/5",  # a read
        "gh api 'search/issues?q=zorblax-quint'",  # a read, even with a literal in the query
        "git log --oneline",
    ],
)
def test_commands_that_send_no_text_are_not_scanned(command):
    result = _run(_bash(command))
    assert result.returncode == 0, result.stderr


def test_other_tools_pass_through():
    result = _run({"tool_name": "Read", "tool_input": {"file_path": "/c/Users/zqfake/x"}})
    assert result.returncode == 0


# --- fail closed --------------------------------------------------------------------------


@pytest.mark.parametrize("literals", [None, "", "---"])
def test_no_literal_list_blocks_outbound_calls_and_says_so(literals):
    for payload in (
        _mcp("create_pull_request", title="x", body="clean"),
        _bash('gh pr create --title x --body "clean"'),
    ):
        result = _run(payload, literals=literals)
        assert result.returncode == 2
        assert "PERSONAL_LITERALS" in result.stderr
        assert "nothing was checked" in result.stderr.lower()


def test_no_literal_list_does_not_block_calls_that_send_nothing():
    result = _run(_bash("git status"), literals=None)
    assert result.returncode == 0


def test_unparseable_input_is_blocked():
    result = _run(None, raw="{not json")
    assert result.returncode == 2


# --- wiring -------------------------------------------------------------------------------


def test_snippet_wires_bash_and_the_whole_github_server_fail_closed():
    snippet = json.loads(SNIPPET.read_text(encoding="utf-8"))
    entries = snippet["hooks"]["PreToolUse"]
    matchers = {e["matcher"]: e["hooks"] for e in entries}
    assert set(matchers) == {"Bash", "^mcp__github__"}
    for hooks in matchers.values():
        (hook,) = hooks
        assert ".agents/hooks/literal-gate.py" in hook["command"]
        assert hook["command"].rstrip().endswith("|| exit 2")
