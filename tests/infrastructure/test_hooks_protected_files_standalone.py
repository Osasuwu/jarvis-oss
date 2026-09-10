"""AC2: .claude/hooks/protected-files.py must fail closed (non-zero exit) on
a protected-file write, invoked exactly as Claude Code invokes a project
hook: as a subprocess with the PreToolUse JSON payload on stdin.

Unlike the user-level hook, the standalone project hook has no
principal-based bypass: canonical AND mirror paths both block unconditionally
(see the module docstring in .claude/hooks/protected-files.py for the
accepted trade-off this implies for the live owner's local edits).
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "protected-files.py"
)


def _run_hook(payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


def test_hook_file_exists():
    assert HOOK_PATH.exists(), ".claude/hooks/protected-files.py must exist"


def test_blocks_canonical_path():
    payload = {"tool_input": {"file_path": ".gitleaks.toml"}}
    result = _run_hook(payload)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "hookSpecificOutput" in result.stdout


def test_blocks_mirror_path_with_no_principal_bypass(tmp_path, monkeypatch):
    """Standalone hook must block a mirror path even though the user-level
    hook would allow it for a live principal — there is no bypass here."""
    fake_home = tmp_path / "claude_home"
    fake_home.mkdir()
    env = {**__import__("os").environ, "JARVIS_CLAUDE_HOME": str(fake_home)}
    payload = {"tool_input": {"file_path": str(fake_home / "settings.json")}}
    result = _run_hook(payload, env=env)
    assert result.returncode == 2, result.stdout + result.stderr


def test_allows_non_protected_path():
    payload = {"tool_input": {"file_path": "src/some_module.py"}}
    result = _run_hook(payload)
    assert result.returncode == 0, result.stdout + result.stderr
