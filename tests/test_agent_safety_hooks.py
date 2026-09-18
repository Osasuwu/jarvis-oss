"""The shipped hook wiring must reach the GitHub MCP write tools that exist today.

Tool names and fields checked against github/github-mcp-server's README on 2026-09-17. A
matcher that lists tools by name fails open silently when the server renames one: the hook
never runs, and nothing reports it. So the matcher takes the whole server, and these tests
pin the text fields the scanner must read.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / ".agents" / "hooks" / "settings.snippet.json"
SCANNER = ROOT / ".agents" / "hooks" / "secret-scanner.py"
PROTECTED_FILES = ROOT / ".agents" / "hooks" / "protected-files.py"

# Write tools and the free-text field each carries that lands on GitHub.
GITHUB_TEXT_WRITE_TOOLS = (
    ("issue_write", "body"),
    ("add_issue_comment", "body"),
    ("update_issue_comment", "body"),
    ("create_pull_request", "body"),
    ("update_pull_request", "body"),
    ("pull_request_review_write", "body"),
    ("add_comment_to_pending_review", "body"),
    ("add_reply_to_pull_request_comment", "body"),
    ("discussion_comment_write", "body"),
    ("projects_write", "body"),
    ("create_pull_request_with_copilot", "problem_statement"),
    ("create_or_update_file", "content"),
    ("delete_file", "message"),
    ("create_repository", "description"),
    ("label_write", "description"),
    ("create_gist", "content"),
    ("update_gist", "content"),
)

# Built by concatenation so this file does not itself trip a secret scanner.
FAKE_KEY = "sk-" + "ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"


def _scanner_matchers() -> list[str]:
    config = json.loads(SNIPPET.read_text(encoding="utf-8"))
    return [
        entry["matcher"]
        for entry in config["hooks"]["PreToolUse"]
        if any("secret-scanner.py" in h["command"] for h in entry["hooks"])
    ]


def _scanned(tool_name: str) -> bool:
    return any(re.search(m, tool_name) for m in _scanner_matchers())


def test_scanner_matcher_covers_current_github_write_tools():
    missing = [t for t, _ in GITHUB_TEXT_WRITE_TOOLS if not _scanned(f"mcp__github__{t}")]
    assert missing == []


def test_scanner_matcher_covers_a_tool_name_it_has_never_seen():
    # A renamed or new server tool must still be scanned: the matcher fails closed.
    assert _scanned("mcp__github__some_future_write_tool")


def test_scanner_matcher_does_not_catch_other_servers():
    assert not _scanned("mcp__other__issue_write")
    assert not _scanned("mcp__not_github__issue_write")


def _run_scanner(tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    return subprocess.run(
        [sys.executable, str(SCANNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(("tool", "field"), GITHUB_TEXT_WRITE_TOOLS)
def test_scanner_blocks_secret_in_each_write_tool_text_field(tool, field):
    result = _run_scanner(f"mcp__github__{tool}", {field: f"key is {FAKE_KEY}"})
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout


def test_scanner_allows_clean_github_read():
    result = _run_scanner("mcp__github__issue_read", {"owner": "o", "repo": "r", "issue_number": 1})
    assert result.returncode == 0


# The scanner must not rely on a fixed field-name whitelist: #74 found
# `custom_instructions`/`rationale` (assign_copilot_to_issue*) and
# `commit_message`/`commit_title` (merge_pull_request) unscanned because
# extract_github_text only read a fixed set of key names. It must scan every
# string value in tool_input, at any nesting depth, regardless of key name.


def test_scanner_blocks_secret_in_custom_instructions_field():
    result = _run_scanner(
        "mcp__github__assign_copilot_to_issue",
        {"owner": "o", "repo": "r", "issueNumber": 1, "custom_instructions": f"use {FAKE_KEY}"},
    )
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout


def test_scanner_blocks_secret_in_rationale_field():
    result = _run_scanner(
        "mcp__github__assign_copilot_to_issue",
        {"owner": "o", "repo": "r", "issueNumber": 1, "rationale": f"because {FAKE_KEY}"},
    )
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout


def test_scanner_blocks_secret_in_merge_commit_message():
    result = _run_scanner(
        "mcp__github__merge_pull_request",
        {"owner": "o", "repo": "r", "pullNumber": 1, "commit_message": f"key {FAKE_KEY}"},
    )
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout


def _all_hook_commands() -> list[str]:
    config = json.loads(SNIPPET.read_text(encoding="utf-8"))
    return [h["command"] for entry in config["hooks"]["PreToolUse"] for h in entry["hooks"]]


def test_every_hook_command_fails_closed_on_launch_failure():
    # A hook that fails to launch (interpreter missing, or too old to parse the
    # script) must deny the call, not let it through. Claude Code treats a
    # non-zero, non-2 exit as non-blocking, so the shipped snippet must chain
    # `|| exit 2` onto every hook invocation.
    commands = _all_hook_commands()
    assert commands, "expected at least one hook command in settings.snippet.json"
    for cmd in commands:
        assert re.search(r"\|\|\s*exit\s+2\s*$", cmd), (
            f"hook command does not fail closed on launch failure: {cmd!r}"
        )


def test_scanner_blocks_secret_in_a_field_name_it_has_never_seen():
    # Nested one level, under a key the scanner's source has never named —
    # pins that scanning is recursive-over-values, not keyed by field name.
    result = _run_scanner(
        "mcp__github__some_future_write_tool",
        {"owner": "o", "repo": "r", "totally_unheard_of_field": {"nested": FAKE_KEY}},
    )
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout


# Protected-path matching must compare canonical filesystem identity, not raw
# strings: #74 found a case-insensitive filesystem let `.Agents/Hooks/
# Secret-Scanner.py` through a case-sensitive string match (exit 0, should be
# exit 2). realpath + normcase on both the input and each protected path also
# closes Windows filename aliases (trailing dot, trailing space, `::$DATA`)
# that a string compare cannot see but the filesystem treats as the same file.


def _run_protected_files(tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    run_env = os.environ.copy()
    run_env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(PROTECTED_FILES)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=run_env,
    )


@pytest.mark.skipif(
    sys.platform not in ("win32", "darwin"),
    reason="case-insensitive-by-default filesystem required to alias the real file",
)
def test_protected_files_blocks_a_case_variant_path():
    target = str(ROOT / ".Agents" / "Hooks" / "Secret-Scanner.py")
    result = _run_protected_files("Edit", {"file_path": target})
    assert result.returncode == 2


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS-specific filename alias, Windows only")
@pytest.mark.parametrize("suffix", [".", " ", "::$DATA"])
def test_protected_files_blocks_ntfs_filename_aliases(suffix):
    target = str(ROOT / ".agents" / "hooks" / "secret-scanner.py") + suffix
    result = _run_protected_files("Edit", {"file_path": target})
    assert result.returncode == 2


def test_protected_files_allows_unprotected_sibling_path():
    target = str(ROOT / ".agents" / "hooks" / "not-a-protected-file.py")
    result = _run_protected_files("Edit", {"file_path": target})
    assert result.returncode == 0
