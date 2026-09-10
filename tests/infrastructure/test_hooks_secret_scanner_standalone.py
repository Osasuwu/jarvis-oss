"""AC2: .claude/hooks/secret-scanner.py must fail closed (non-zero exit) on a
detected secret, invoked exactly as Claude Code invokes a project hook: as a
subprocess with the PreToolUse JSON payload on stdin, no repo package context.

Fixture secrets below are built via string concatenation (not literal
contiguous strings) so authoring this file doesn't itself trip the
write-time secret scanner active in this repo.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "secret-scanner.py"
)

_FAKE_AWS_KEY = "AKIA" + "0123456789ABCDEF"
_FAKE_ANTHROPIC_KEY = "sk-ant-" + ("a" * 25)


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_hook_file_exists():
    assert HOOK_PATH.exists(), ".claude/hooks/secret-scanner.py must exist"


def test_blocks_bash_command_with_secret():
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "export AWS_KEY=" + _FAKE_AWS_KEY},
    }
    result = _run_hook(payload)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "hookSpecificOutput" in result.stdout


def test_blocks_file_write_with_secret():
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "notes.txt",
            "content": "key value is " + _FAKE_ANTHROPIC_KEY,
        },
    }
    result = _run_hook(payload)
    assert result.returncode == 2, result.stdout + result.stderr


def test_allows_benign_bash_command():
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
    result = _run_hook(payload)
    assert result.returncode == 0, result.stdout + result.stderr


def test_allows_benign_file_write():
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "notes.txt", "content": "hello world"},
    }
    result = _run_hook(payload)
    assert result.returncode == 0, result.stdout + result.stderr
