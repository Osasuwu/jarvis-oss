"""The literal gate and the authority guard find the same gh writes (#278).

Both hooks decide "is this a gh write" through `.agents/hooks/gh_write.py`. What each does
with a write stays its own: the guard blocks a merge, a hold-label removal or a protection
write; the literal gate scans the text the write sends. So every shape is checked through both
hooks' real scripts, each with its own verdict:

- the guard blocks the shape wrapped around `gh pr merge` and lets `gh pr view` through;
- the literal gate blocks it wrapped around a comment holding a literal, lets the same comment
  through when it is clean, and does not scan a read.

With no literal list, the literal gate blocks every gh write and nothing else, which makes it a
direct read of its write detection; that is how the guard's own blocked routes are walked.

Every literal in this file is fake.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".agents" / "hooks" / "github-authority-guard.py"
LITERAL_GATE = ROOT / ".agents" / "hooks" / "literal-gate.py"

FAKE_LITERALS = "Zorblax Quint\nC:\\Users\\zqfake"
LITERAL = "zorblax-quint"
NO_LIST = "No personal literals configured"


def _guard_blocks(command: str, tool: str = "Bash") -> bool:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": tool, "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 2), result.stderr
    if result.returncode == 2:
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    return result.returncode == 2


def _literal_gate(command: str, tool: str = "Bash", literals: str | None = FAKE_LITERALS):
    with tempfile.TemporaryDirectory() as empty_home:
        env = dict(os.environ)
        env.pop("PERSONAL_LITERALS", None)
        if literals is not None:
            env["PERSONAL_LITERALS"] = literals
        env["HOME"] = env["USERPROFILE"] = empty_home  # no developer allowlist
        return subprocess.run(
            [sys.executable, str(LITERAL_GATE)],
            input=json.dumps({"tool_name": tool, "tool_input": {"command": command}}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )


def _assert_literal_hit(result) -> None:
    assert result.returncode == 2, (result.returncode, result.stderr)
    assert "A personal literal" in result.stderr, result.stderr
    assert "zorblax" not in (result.stdout + result.stderr).lower()


# A shape is a template around one gh call: {g} {v} {a} are its group, verb and arguments
# (`gh {g} {v} {a}`), so a flag can sit between the two words as cobra allows.
SHAPES = [
    ("bare", "gh {g} {v} {a}", "Bash"),
    ("gh.exe", "gh.exe {g} {v} {a}", "Bash"),
    ("GH.EXE", "GH.EXE {g} {v} {a}", "Bash"),
    ("double-quoted", '"gh" {g} {v} {a}', "Bash"),
    ("single-quoted exe", "'gh.exe' {g} {v} {a}", "Bash"),
    ("quoted path with spaces", '"C:/Program Files/GitHub CLI/gh.exe" {g} {v} {a}', "Bash"),
    ("quoted backslash path", "'C:\\Program Files\\GitHub CLI\\gh.exe' {g} {v} {a}", "Bash"),
    ("unquoted backslash path", "C:\\tools\\gh.exe {g} {v} {a}", "PowerShell"),
    ("posix path", "/usr/bin/gh {g} {v} {a}", "Bash"),
    ("PowerShell call operator", '& "gh.exe" {g} {v} {a}', "PowerShell"),
    ("-R before the subcommand", "gh -R o/r {g} {v} {a}", "Bash"),
    ("--repo= before the subcommand", "gh --repo=o/r {g} {v} {a}", "Bash"),
    ("-R glued to its value", "gh -Ro/r {g} {v} {a}", "Bash"),
    ("--hostname before the subcommand", "gh --hostname github.example {g} {v} {a}", "Bash"),
    ("-R between group and verb", "gh {g} -R o/r {v} {a}", "Bash"),
    ("bash -c", 'bash -c "gh {g} {v} {a}"', "Bash"),
    ("sh -c", "sh -c 'gh -R o/r {g} {v} {a}'", "Bash"),
    ("bash -lc", 'bash -lc "gh {g} {v} {a}"', "Bash"),
    ("bash -c in bash -c", "bash -c \"bash -c 'gh {g} {v} {a}'\"", "Bash"),
    ("pwsh -c", 'pwsh -c "gh {g} {v} {a}"', "Bash"),
    ("powershell -Command", 'powershell -Command "gh {g} {v} {a}"', "PowerShell"),
    ("pwsh -CommandWithArgs", "pwsh -CommandWithArgs 'gh {g} {v} {a}'", "Bash"),
    ("cmd /c", 'cmd /c "gh {g} {v} {a}"', "Bash"),
    ("eval", 'eval "gh {g} {v} {a}"', "Bash"),
    ("Invoke-Expression", "Invoke-Expression 'gh {g} {v} {a}'", "PowerShell"),
    ("ssh remote command", 'ssh -p 2222 -o BatchMode=yes host "gh {g} {v} {a}"', "Bash"),
    ("backticks", "echo `gh {g} {v} {a}`", "Bash"),
    ("command substitution", "echo $(gh {g} {v} {a})", "Bash"),
    ("after another step", "git push && gh {g} {v} {a}", "Bash"),
    # A heredoc fed to an interpreter is its standard input: the body runs (#285).
    ("bash heredoc", "bash <<'EOF'\ngh {g} {v} {a}\nEOF", "Bash"),
    ("ssh heredoc", "ssh host <<EOF\necho start\ngh {g} {v} {a}\nEOF", "Bash"),
    ("tab-stripped heredoc to a path-qualified shell, after a step",
     "git fetch && /usr/bin/bash -s <<-EOF\n\tgh {g} {v} {a}\n\tEOF\necho done", "Bash"),
    # `bash -c` runs what `cat` prints, so the body is the script.
    ("bash -c substitution of a heredoc", "bash -c \"$(cat <<'EOF'\ngh {g} {v} {a}\nEOF\n)\"", "Bash"),
]


@pytest.mark.parametrize(("template", "tool"), [s[1:] for s in SHAPES], ids=[s[0] for s in SHAPES])
def test_both_hooks_gate_the_same_shape(template, tool):
    def shape(g, v, a):
        return template.format(g=g, v=v, a=a)

    assert _guard_blocks(shape("pr", "merge", "5"), tool), "guard let the merge through"
    assert not _guard_blocks(shape("pr", "view", "5"), tool), "guard blocked a read"
    _assert_literal_hit(_literal_gate(shape("pr", "comment", f"5 --body {LITERAL}"), tool))
    clean = _literal_gate(shape("pr", "comment", "5 --body clean"), tool)
    assert clean.returncode == 0, clean.stderr
    read = _literal_gate(shape("issue", "list", f"--search {LITERAL}"), tool)
    assert read.returncode == 0, read.stderr


# A heredoc that writes text (a file, a commit message) is not run, so a gh write named in
# its body is not a gh write (#285). `deploy.sh` names a script file, not an interpreter.
TEXT_HEREDOCS = {
    "cat to file": "cat > notes.md <<'EOF'\ngh pr merge 5\nEOF",
    "git commit -F -": "git commit -F - <<'EOF'\ndocs: gh pr merge 5\nEOF",
    "cat to a .sh file": "cat > deploy.sh <<'EOF'\ngh pr merge 5\nEOF",
}


@pytest.mark.parametrize("command", TEXT_HEREDOCS.values(), ids=TEXT_HEREDOCS.keys())
def test_neither_hook_reads_a_text_heredoc_body_as_a_gh_write(command):
    assert not _guard_blocks(command), "guard blocked a merge that is only text"
    # With no literal list the literal gate blocks every gh write it finds, and only those.
    result = _literal_gate(command, literals=None)
    assert result.returncode == 0, result.stderr


# Writes the guard blocks whatever their text, which also carry text the literal gate scans.
BODY_WRITES = [
    "gh api -X PATCH repos/o/r/issues/5 -f labels[]=x -f body={body}",
    "gh -R o/r api -X PUT repos/o/r/pulls/5/merge -f commit_message={body}",
    "gh label edit waiting-human-review --description {body}",
    "gh api -X DELETE repos/o/r/labels/waiting-human-review -f note={body}",
]


@pytest.mark.parametrize("template", BODY_WRITES)
def test_a_guarded_write_is_also_scanned_for_literals(template):
    assert _guard_blocks(template.format(body="clean"))
    _assert_literal_hit(_literal_gate(template.format(body=LITERAL)))
    clean = _literal_gate(template.format(body="clean"))
    assert clean.returncode == 0, clean.stderr


def test_api_input_to_an_issue_is_guarded_and_its_file_scanned(tmp_path: Path):
    body = tmp_path / "body.json"
    command = f'gh api -X PATCH repos/o/r/issues/5 --input "{body.as_posix()}"'
    body.write_text('{"body": "zorblax quint"}', encoding="utf-8")
    assert _guard_blocks(command)
    _assert_literal_hit(_literal_gate(command))
    body.write_text('{"body": "clean"}', encoding="utf-8")
    assert _literal_gate(command).returncode == 0


def _encoded(command: str) -> str:
    """pwsh -EncodedCommand takes base64 of UTF-16LE text."""
    return "pwsh -EncodedCommand " + base64.b64encode(command.encode("utf-16-le")).decode()


def test_an_encoded_gh_write_is_blocked_by_both_and_an_encoded_read_by_neither():
    assert _guard_blocks(_encoded("gh pr merge 5"))
    assert not _guard_blocks(_encoded("gh pr view 5"))
    # The literal gate cannot scan text it only sees encoded, clean or not.
    hidden = _literal_gate(_encoded("gh pr comment 5 --body clean"))
    assert hidden.returncode == 2, hidden.stderr
    assert "-EncodedCommand" in hidden.stderr, hidden.stderr
    read = _literal_gate(_encoded("gh issue list --search zorblax-quint"))
    assert read.returncode == 0, read.stderr


def _guard_blocked_shell() -> list[str]:
    spec = importlib.util.spec_from_file_location(
        "guard_tests_for_parity", ROOT / "tests" / "test_github_authority_guard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BLOCKED_SHELL


def test_literal_gate_treats_every_guard_gh_route_as_a_write():
    """Each gh route the guard's tests block is a gh write the literal gate gates: with no
    literal list it blocks exactly the gh writes. Raw HTTP routes are not gh calls."""
    routes = [c for c in _guard_blocked_shell() if "api.github.com" not in c]
    assert len(routes) > 40, len(routes)
    missed = []
    for command in routes:
        result = _literal_gate(command, literals=None)
        if result.returncode != 2 or NO_LIST not in result.stderr:
            missed.append(command)
    assert missed == []
