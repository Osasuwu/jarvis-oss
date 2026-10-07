"""Tests for the literal-gate PreToolUse hook (#100 AC3).

Mechanism under test: `.agents/hooks/literal-gate.py`, wired by the matchers in
`.agents/hooks/settings.snippet.json` on `Bash|PowerShell` and on the whole GitHub MCP
server under any prefix (`^mcp__.*github`), the same matchers as the authority guard (#99). It reads the tool call as JSON on stdin and exits 2 to block when the text
about to be sent to GitHub (a PR or issue title/body, a comment, a review, a release note, a
`gh api` write) holds any variant of a personal literal. The hook never prints the literal.

Every literal in this file is fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".agents" / "hooks" / "literal-gate.py"
SNIPPET = ROOT / ".agents" / "hooks" / "settings.snippet.json"

FAKE_LITERAL = "Zorblax Quint"
FAKE_PATH_LITERAL = r"C:\Users\zqfake"
FAKE_VARIANTS = ("zorblax-quint", "ZORBLAX_QUINT", "ZorblaxQuint", "zorblax quint", "zqfake")


def _run(
    payload,
    literals: str | None = f"{FAKE_LITERAL}\n{FAKE_PATH_LITERAL}",
    raw=None,
    home: Path | None = None,
    extra_env: dict[str, str] | None = None,
):
    """Run the hook. Home is an empty directory unless one is given, so the developer's own
    repo allowlist never reaches a test."""
    with tempfile.TemporaryDirectory() as empty_home:
        env = dict(os.environ)
        env.pop("PERSONAL_LITERALS", None)
        if literals is not None:
            env["PERSONAL_LITERALS"] = literals
        env["HOME"] = env["USERPROFILE"] = str(home or empty_home)
        env.update(extra_env or {})
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
    assert "could not read" not in result.stderr.lower()
    assert "body file" in result.stderr


def test_body_file_short_flag_and_relative_path_resolve_against_cwd(tmp_path: Path):
    (tmp_path / "body.md").write_text("ZORBLAX_QUINT\n", encoding="utf-8")
    result = _run(_bash("gh issue create -t x -F body.md", cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "could not read" not in result.stderr.lower()
    assert "body file" in result.stderr


def test_body_file_relative_to_a_cd_in_the_same_command(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "body.md").write_text("zorblax quint\n", encoding="utf-8")
    command = f'cd "{sub.as_posix()}" && gh pr comment 3 --body-file body.md'
    result = _run(_bash(command, cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "could not read" not in result.stderr.lower()
    assert "body file" in result.stderr


def test_gh_api_field_from_file_is_scanned(tmp_path: Path):
    (tmp_path / "b.md").write_text("zorblax quint\n", encoding="utf-8")
    command = "gh api repos/o/r/issues/1/comments -F body=@b.md"
    result = _run(_bash(command, cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "could not read" not in result.stderr.lower()
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


def _powershell(command: str) -> dict:
    return {"tool_name": "PowerShell", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --title x --body 'zorblax quint'",
        "Set-Location C:/x; gh issue comment 5 --body 'ZorblaxQuint'",
        "@'\nsummary\nzorblax quint\n'@ | gh pr create --title x --body-file -",
    ],
)
def test_powershell_gh_write_with_a_literal_variant_is_blocked(command):
    _assert_blocked_quietly(_run(_powershell(command)))


def test_powershell_body_file_relative_to_a_set_location(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "body.md").write_text("zorblax quint\n", encoding="utf-8")
    command = f'Set-Location "{sub.as_posix()}"; gh pr comment 3 --body-file body.md'
    payload = _powershell(command)
    payload["cwd"] = str(tmp_path)
    result = _run(payload)
    _assert_blocked_quietly(result)
    assert "could not read" not in result.stderr.lower()
    assert "body file" in result.stderr


def test_powershell_here_string_piped_as_the_body_is_scanned_not_refused():
    command = "@'\nnothing private\n'@ | gh pr create --title x --body-file -"
    result = _run(_powershell(command))
    assert result.returncode == 0, result.stderr


def test_github_mcp_server_under_another_prefix_is_scanned():
    payload = {
        "tool_name": "mcp__plugin_github_github__create_pull_request",
        "tool_input": {"title": "x", "body": "zorblax quint"},
    }
    _assert_blocked_quietly(_run(payload))


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


# --- chained commands: only the gh steps are sent (#233) ------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls /c/Users/zqfake && gh pr create --title fix --body clean",
        "gh pr comment 1 --body clean; rm -rf /c/Users/zqfake/scratch",
        "mkdir -p /c/Users/zqfake/x\ngh issue comment 2 --body clean || echo /c/Users/zqfake",
    ],
)
def test_a_home_path_in_a_step_that_is_not_gh_does_not_block(command):
    result = _run(_bash(command))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "B='zorblax quint'; gh pr comment 1 --body \"$B\"",  # a variable set by an earlier step
        "echo zorblax quint | xargs gh pr comment 1 --body",  # fed by a pipe
    ],
)
def test_a_gh_step_that_takes_text_from_earlier_steps_is_scanned_with_them(command):
    _assert_blocked_quietly(_run(_bash(command)))


def test_a_separator_inside_quotes_does_not_split_the_step():
    result = _run(_bash("gh pr comment 1 --body 'see && ls /c/Users/zqfake; done'"))
    _assert_blocked_quietly(result)


def test_a_heredoc_body_belongs_to_the_step_that_opens_it():
    command = "gh pr comment 1 --body-file - <<'EOF'\nzorblax quint\nEOF\nls /tmp"
    _assert_blocked_quietly(_run(_bash(command)))


def test_a_command_that_cannot_be_split_is_scanned_whole():
    command = "ls /c/Users/zqfake && gh pr create --title fix --body clean 'unclosed"
    result = _run(_bash(command))
    _assert_blocked_quietly(result)
    assert "at: the command, line 1:5." in result.stderr


# --- where the hit is, and the allowed route ------------------------------------------------


def test_command_hit_is_located_by_line_and_column():
    command = "ls /c/Users/zqfake && gh pr comment 1 --body 'clean\nalso zorblax quint'"
    result = _run(_bash(command))
    _assert_blocked_quietly(result)
    assert "at: the command, line 2:6." in result.stderr


def test_body_file_hit_is_located_by_line_and_column(tmp_path: Path):
    (tmp_path / "body.md").write_text("## Summary\nhi zorblax quint\n", encoding="utf-8")
    result = _run(_bash("gh pr comment 1 --body-file body.md", cwd=tmp_path))
    _assert_blocked_quietly(result)
    assert "at: body file body.md, line 2:4." in result.stderr


def test_mcp_hit_is_located_by_field_line_and_column():
    result = _run(_mcp("add_issue_comment", issue_number=1, body="ok\n  zorblax quint"))
    _assert_blocked_quietly(result)
    assert "at: field body, line 2:3." in result.stderr


def test_bash_refusal_names_the_allowed_route_for_a_body():
    result = _run(_bash("gh pr comment 1 --body 'zorblax quint'"))
    _assert_blocked_quietly(result)
    assert "outside the working tree" in result.stderr
    assert "--body-file" in result.stderr


def test_body_file_under_git_bash_tmp_is_read(tmp_path: Path):
    """Git Bash's `/tmp` is the Windows temp directory; on other systems it is `/tmp`."""
    if os.name == "nt":
        temp_dir, extra_env = tmp_path, {"TEMP": str(tmp_path), "TMP": str(tmp_path)}
    else:
        temp_dir, extra_env = Path("/tmp"), {}
    fd, name = tempfile.mkstemp(suffix=".md", dir=temp_dir)
    os.close(fd)
    body = Path(name)
    try:
        body.write_text("zorblax quint\n", encoding="utf-8")
        command = f"gh pr create --title x --body-file /tmp/{body.name}"
        result = _run(_bash(command), extra_env=extra_env)
    finally:
        body.unlink()
    _assert_blocked_quietly(result)
    assert f"at: body file /tmp/{body.name}, line 1:1." in result.stderr


def test_body_file_held_in_a_shell_variable_is_refused_with_how_to_pass():
    result = _run(_bash('gh pr create --title x --body-file "$TMP/body.md"'))
    assert result.returncode == 2
    assert "held in a shell variable" in result.stderr
    assert "Pass a literal path." in result.stderr
    assert "outside the working tree" in result.stderr


def test_body_file_named_by_an_earlier_step_is_refused_with_how_to_pass(tmp_path: Path):
    (tmp_path / "body.md").write_text("an old clean body\n", encoding="utf-8")
    command = "printf 'new body' > body.md && gh pr comment 1 --body-file body.md"
    result = _run(_bash(command, cwd=tmp_path))
    assert result.returncode == 2
    assert "named by an earlier step of this same command" in result.stderr
    assert "Write the body file in a prior call" in result.stderr


def test_missing_body_file_is_refused_with_how_to_pass(tmp_path: Path):
    result = _run(_bash("gh pr comment 1 --body-file missing.md", cwd=tmp_path))
    assert result.returncode == 2
    assert "missing.md: it does not exist (yet)" in result.stderr
    assert "Write it in a prior call." in result.stderr


# --- scope by target repository (#233) -----------------------------------------------------


def _home_with_allowlist(tmp_path: Path, *lines: str) -> Path:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    allowlist = home / ".config" / "literal-gate-allowed-repos.txt"
    allowlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return home


@pytest.mark.parametrize(
    "command",
    [
        "gh -R me/PRIV pr comment 1 --body 'zorblax quint'",
        "gh pr comment 1 --repo=me/priv --body 'zorblax quint'",
        "gh issue create -R https://github.com/me/priv.git -t x -b 'zorblax quint'",
        "gh api repos/me/priv/issues -f title=x -f body='zorblax quint'",
        "gh api -X POST repos/me/priv/issues/1/comments -f body='zorblax quint'",
    ],
)
def test_write_to_an_allowlisted_repo_is_not_checked(tmp_path: Path, command):
    home = _home_with_allowlist(tmp_path, "# private repos only", "ME/priv")
    result = _run(_bash(command), home=home)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh pr comment 1 --body 'zorblax quint'",  # no repo named: the current one
        "gh -R o/r pr comment 1 --body 'zorblax quint'",  # not on the list
        "gh api repos/o/r/issues -f body='zorblax quint'",
        "gh -R me/priv pr comment 1 -R o/r --body 'zorblax quint'",  # two targets
        "GH_REPO=o/r gh -R me/priv pr comment 1 --body 'zorblax quint'",
        "gh -R me/priv pr comment 1 --body 'zorblax quint' )",  # cannot be split
    ],
)
def test_write_to_a_repo_not_on_the_allowlist_is_checked(tmp_path: Path, command):
    home = _home_with_allowlist(tmp_path, "me/priv")
    _assert_blocked_quietly(_run(_bash(command), home=home))


def test_missing_allowlist_allowlists_nothing():
    _assert_blocked_quietly(_run(_bash("gh -R me/priv pr comment 1 --body 'zorblax quint'")))


def test_target_is_read_per_gh_step(tmp_path: Path):
    home = _home_with_allowlist(tmp_path, "me/priv")
    to_private_then_public = (
        "gh -R me/priv issue comment 2 --body 'zorblax quint' && "
        "gh -R o/r pr comment 1 --body 'zorblax quint'"
    )
    result = _run(_bash(to_private_then_public), home=home)
    _assert_blocked_quietly(result)
    assert "at: the command, line 1:88." in result.stderr
    clean_public_then_private = (
        "gh -R o/r pr comment 1 --body clean && "
        "gh -R me/priv issue comment 2 --body 'zorblax quint'"
    )
    assert _run(_bash(clean_public_then_private), home=home).returncode == 0


def test_mcp_write_to_an_allowlisted_repo_is_not_checked(tmp_path: Path):
    home = _home_with_allowlist(tmp_path, "me/priv")
    private = _mcp(
        "add_issue_comment", owner="Me", repo="Priv", issue_number=1, body="zorblax quint"
    )
    assert _run(private, home=home).returncode == 0
    public = _mcp("add_issue_comment", owner="o", repo="r", issue_number=1, body="zorblax quint")
    _assert_blocked_quietly(_run(public, home=home))


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


def test_snippet_wires_both_shells_and_the_whole_github_server_fail_closed():
    """Same matchers as the authority guard in settings.snippet.json (#99)."""
    snippet = json.loads(SNIPPET.read_text(encoding="utf-8"))
    wired = [
        (entry["matcher"], hook["command"])
        for entry in snippet["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
        if ".agents/hooks/literal-gate.py" in hook["command"]
    ]
    assert sorted(m for m, _ in wired) == sorted(["Bash|PowerShell", "^mcp__.*github"])
    for _, command in wired:
        assert command.rstrip().endswith("|| exit 2")


def test_no_separate_literal_gate_snippet():
    """The canonical snippet is the one place the gate is wired (#122)."""
    assert not (ROOT / ".agents" / "hooks" / "literal-gate.snippet.json").exists()


def test_hook_script_is_protected_from_agent_edits():
    text = (ROOT / ".agents" / "hooks" / "protected-files.py").read_text(encoding="utf-8")
    assert '".agents/hooks/literal-gate.py"' in text
