"""The shipped hook wiring must reach the GitHub MCP write tools that exist today.

Tool names checked against github/github-mcp-server's README on 2026-09-17. A matcher that
names retired tools fails open silently: the hook never runs, and nothing reports it.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / ".agents" / "hooks" / "settings.snippet.json"
SCANNER = ROOT / ".agents" / "hooks" / "secret-scanner.py"

# Write tools whose inputs carry free text that lands on GitHub.
GITHUB_TEXT_WRITE_TOOLS = (
    "issue_write",
    "add_issue_comment",
    "update_issue_comment",
    "create_pull_request",
    "update_pull_request",
    "pull_request_review_write",
    "add_comment_to_pending_review",
    "add_reply_to_pull_request_comment",
    "create_or_update_file",
    "push_files",
    "create_gist",
    "update_gist",
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
    missing = [t for t in GITHUB_TEXT_WRITE_TOOLS if not _scanned(f"mcp__github__{t}")]
    assert missing == []


def test_scanner_matcher_does_not_catch_read_tools_or_other_servers():
    assert not _scanned("mcp__github__issue_read")
    assert not _scanned("mcp__github__list_issues")
    assert not _scanned("mcp__other__issue_write")


def test_scanner_blocks_secret_in_issue_write_body():
    payload = {
        "tool_name": "mcp__github__issue_write",
        "tool_input": {"method": "create", "title": "bug", "body": f"key is {FAKE_KEY}"},
    }
    result = subprocess.run(
        [sys.executable, str(SCANNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "permissionDecision" in result.stdout
