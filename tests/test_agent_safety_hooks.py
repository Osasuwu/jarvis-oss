"""The shipped hook wiring must reach the GitHub MCP write tools that exist today.

Tool names and fields checked against github/github-mcp-server's README on 2026-09-17. A
matcher that lists tools by name fails open silently when the server renames one: the hook
never runs, and nothing reports it. So the matcher takes the whole server, and these tests
pin the text fields the scanner must read.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / ".agents" / "hooks" / "settings.snippet.json"
SCANNER = ROOT / ".agents" / "hooks" / "secret-scanner.py"

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
