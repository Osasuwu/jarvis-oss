"""Every command in settings.snippet.json must find a working Python and still fail closed (#181).

On Windows, `python3` can be the Microsoft Store alias stub: it is on PATH, runs, prints an
install hint and exits non-zero (9009 in #181). A command that calls `python3` directly then exits non-zero on
every call, and its `|| exit 2` turns that into a deny for every matched tool call. So each
command probes `python3`, falls back to `python`, and still ends in `|| exit 2` for the case
where neither starts.

The tests run each command the way the harness does, as a shell command line with the tool call
on stdin, with stub interpreters first on PATH.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / ".agents" / "hooks" / "settings.snippet.json"

# Built by concatenation so this file does not itself trip a secret scanner.
FAKE_KEY = "sk-" + "ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"
FAKE_LITERAL = "Zorblax Quint"


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _write(path: Path, content: str) -> dict:
    return {
        "tool_name": "Write",
        "tool_input": {"file_path": str(path), "content": content},
    }


# Per hook script: a tool call it lets through, and one it denies.
PAYLOADS = {
    "secret-scanner.py": (
        _write(ROOT / "scratch.txt", "hello"),
        _write(ROOT / "scratch.txt", f"key is {FAKE_KEY}"),
    ),
    "protected-files.py": (
        _write(ROOT / "scratch.txt", "hello"),
        _write(ROOT / ".agents" / "hooks" / "secret-scanner.py", "x"),
    ),
    "github-authority-guard.py": (_bash("gh pr view 12"), _bash("gh pr merge 12")),
    "literal-gate.py": (
        _bash("echo hello"),
        _bash(f'gh issue comment 1 --body "thanks {FAKE_LITERAL}"'),
    ),
}

# The Store stub's behaviour: an install hint on stderr and exit 9009. The second one also
# reads stdin, so a probe that does not shield stdin hands the hook an empty payload.
STUB = '#!/bin/sh\necho "Python was not found; run without arguments to install" >&2\nexit 9009\n'
STDIN_EATING_STUB = (
    '#!/bin/sh\ncat >/dev/null\necho "Python was not found" >&2\nexit 9009\n'
)

SHELLS = [s for s in ("sh", "bash") if shutil.which(s)]


def _commands() -> list[tuple[str, str]]:
    config = json.loads(SNIPPET.read_text(encoding="utf-8"))
    out = []
    for entry in config["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            script = re.search(r"\.agents/hooks/([\w-]+\.py)", hook["command"]).group(1)
            out.append((script, hook["command"]))
    return out


COMMANDS = _commands()
IDS = [f"{i}-{script}" for i, (script, _) in enumerate(COMMANDS)]


def _stub_dir(tmp_path: Path, python3: str, python: str | None) -> Path:
    """A PATH directory whose `python3` is `python3`; `python` is `python`, or the real one."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = Path(sys.executable).as_posix()
    files = {"python3": python3, "python": python or f'#!/bin/sh\nexec "{real}" "$@"\n'}
    for name, text in files.items():
        path = bin_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    return bin_dir


def _run(
    shell: str, command: str, payload: dict, bin_dir: Path
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    env["PERSONAL_LITERALS"] = FAKE_LITERAL
    return subprocess.run(
        [shutil.which(shell), "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=ROOT,
        timeout=60,
    )


pytestmark = pytest.mark.skipif(not SHELLS, reason="no POSIX shell on PATH")


def test_every_hook_script_has_payloads():
    assert COMMANDS, "expected hook commands in settings.snippet.json"
    assert {script for script, _ in COMMANDS} <= set(PAYLOADS)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("script", "command"), COMMANDS, ids=IDS)
def test_stub_python3_falls_back_and_allows_a_clean_call(
    tmp_path, shell, script, command
):
    bin_dir = _stub_dir(tmp_path, python3=STUB, python=None)
    result = _run(shell, command, PAYLOADS[script][0], bin_dir)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "stub", [STUB, STDIN_EATING_STUB], ids=["stub", "stdin-eating-stub"]
)
@pytest.mark.parametrize(("script", "command"), COMMANDS, ids=IDS)
def test_stub_python3_falls_back_and_still_denies(
    tmp_path, shell, stub, script, command
):
    bin_dir = _stub_dir(tmp_path, python3=stub, python=None)
    result = _run(shell, command, PAYLOADS[script][1], bin_dir)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    # The deny came from the hook, not from a launch failure: every hook names the block,
    # in a deny decision on stdout or on stderr (literal-gate).
    assert "blocked" in (result.stdout + result.stderr).lower(), (
        result.stdout,
        result.stderr,
    )


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("script", "command"), COMMANDS, ids=IDS)
def test_no_working_interpreter_denies(tmp_path, shell, script, command):
    bin_dir = _stub_dir(tmp_path, python3=STUB, python=STUB)
    result = _run(shell, command, PAYLOADS[script][0], bin_dir)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
