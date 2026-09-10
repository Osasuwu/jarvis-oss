"""AC3: .claude/settings.json must register the two standalone project hooks
(secret-scanner, protected-files) as PreToolUse hooks, loadable in a fresh
session, with commands anchored to $CLAUDE_PROJECT_DIR so they resolve
correctly in CI/Actions checkouts as well as local worktrees.
"""

import json
import re
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent.parent / ".claude" / "settings.json"

_COMMAND_RE = re.compile(r"^python \$CLAUDE_PROJECT_DIR/\.claude/hooks/([\w-]+\.py)$")


def _load_settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def _all_commands(settings: dict) -> list[str]:
    commands = []
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        for hook in entry.get("hooks", []):
            commands.append(hook["command"])
    return commands


def test_settings_file_is_valid_json():
    _load_settings()  # raises on parse failure


def test_settings_registers_exactly_two_distinct_hook_scripts():
    settings = _load_settings()
    commands = _all_commands(settings)
    assert commands, ".claude/settings.json must register at least one PreToolUse hook"

    scripts = set()
    for command in commands:
        match = _COMMAND_RE.match(command)
        assert match, (
            f"hook command not $CLAUDE_PROJECT_DIR-anchored under .claude/hooks/: {command!r}"
        )
        scripts.add(match.group(1))

    assert scripts == {"secret-scanner.py", "protected-files.py"}, scripts


def test_edit_write_matcher_wires_both_scripts():
    settings = _load_settings()
    for entry in settings["hooks"]["PreToolUse"]:
        if entry["matcher"] == "Edit|Write|NotebookEdit":
            scripts = {_COMMAND_RE.match(h["command"]).group(1) for h in entry["hooks"]}
            assert scripts == {"secret-scanner.py", "protected-files.py"}
            return
    raise AssertionError("no Edit|Write|NotebookEdit matcher found in .claude/settings.json")


def test_bash_matcher_wires_secret_scanner_only():
    settings = _load_settings()
    for entry in settings["hooks"]["PreToolUse"]:
        if entry["matcher"] == "Bash":
            scripts = {_COMMAND_RE.match(h["command"]).group(1) for h in entry["hooks"]}
            assert scripts == {"secret-scanner.py"}
            return
    raise AssertionError("no Bash matcher found in .claude/settings.json")
