"""Layer A of the #1816 code gate: ``ruff check`` restricted to lint group 1.

Coverage required by the #1816 AC:
- ``pyproject.toml`` pins ``[tool.ruff.lint].select`` to exactly group 1
  (``F``, ``B``, ``E722``) — no more, no less.
- ``ruff check`` actually passes clean on the real repo surface with that
  config, with no ``# noqa`` baseline propping it up.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_ruff_lint_select() -> list[str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["ruff"]["lint"]["select"]


def test_ruff_lint_select_is_exactly_group_1() -> None:
    select = _load_ruff_lint_select()
    assert set(select) == {"F", "B", "E722"}


def test_ruff_check_passes_clean_on_repo_surface() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff check (group 1) is not clean on the repo surface:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
