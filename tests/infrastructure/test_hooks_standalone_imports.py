"""AC1: .claude/hooks/*.py must be standalone — zero imports from scripts/lib,
hook-resolver, or principal. They run as project-level hooks, invoked from a
plain `python <script>` with no repo package context (and in CI/Actions,
which never has the ~/.claude or scripts/lib environment set up).
"""

import ast
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
HOOK_FILES = ("secret-scanner.py", "protected-files.py", "device-info.py")

_FORBIDDEN_MODULES = ("lib", "lib.harness", "hook_resolver", "hook-resolver", "principal")


def _imported_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


@pytest.mark.parametrize("filename", HOOK_FILES)
def test_hook_file_exists(filename):
    assert (HOOKS_DIR / filename).exists(), f".claude/hooks/{filename} must exist"


@pytest.mark.parametrize("filename", HOOK_FILES)
def test_hook_has_no_forbidden_imports(filename):
    path = HOOKS_DIR / filename
    if not path.exists():
        pytest.fail(f".claude/hooks/{filename} must exist")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = _imported_names(tree)
    for forbidden in _FORBIDDEN_MODULES:
        matches = {
            name for name in imported if name == forbidden or name.startswith(forbidden + ".")
        }
        assert not matches, f"{filename} imports forbidden module(s): {matches}"
