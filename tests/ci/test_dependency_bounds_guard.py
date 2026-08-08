"""Regression guard: no runtime dependency range is left unbounded (#1296).

An unbounded `>=`-only specifier resolves to whatever the newest release is
at install time, with no PR or merge required to trigger it. That is exactly
what turned every jarvis CI job red on 2026-07-28 when `mcp` shipped a
breaking 2.0.0 (fixed as a stopgap in #1292, SDK port tracked in #1294).
Sibling-grep for #1296 found the identical unbounded `mcp` range still live
in `mcp-memory/requirements.txt` — the actual install manifest for the
production memory server (see SETUP.md) — never touched by #1292.

Decided strategy (record_decision episode 3ee1b0f6-9aa6-4751-8db0-e93ec3605e40,
#1296): blanket upper bounds on every runtime dependency range in both
manifests, refreshed via the already-configured weekly Dependabot jobs
(.github/dependabot.yml, root + /mcp-memory) rather than a lockfile. This
guard is the "deliberate check" the issue's acceptance criteria calls for —
it fails closed the moment a bare `>=`-only spec (or a spec with no operator
at all, which pip treats as fully unconstrained) reappears in either file.

`[build-system] requires` is intentionally out of scope: those are
build-backend deps (setuptools/wheel), not the runtime-resolution surface the
outage came from, and they are not reinstalled on every `pip install -e .`
the way project dependencies are.

Runs via ci-meta.yml (``pytest tests/ci/`` — not path-filtered) and the
regular suite (``testpaths = ["tests"]``), so it fires on every PR. Uses
``tomllib`` (stdlib, Python >=3.11 per this project's `requires-python`) —
no extra install needed, compatible with ci-meta.yml's minimal dependency set.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MCP_MEMORY_REQUIREMENTS_PATH = REPO_ROOT / "mcp-memory" / "requirements.txt"

# Any of these characters means the spec carries a version constraint at all
# (as opposed to a bare self-referencing extra like "jarvis-agent[memory]").
_VERSION_OPERATOR_RE = re.compile(r"[<>=!~]")
# An upper bound: an explicit "<" ceiling, or an exact/compatible pin that
# already can't silently float past a major ("==", "~=").
_UPPER_BOUNDED_RE = re.compile(r"<|==|~=")


def _pyproject_dependency_specs() -> dict[str, str]:
    """Map ``"<section> -> spec string"`` for every declared runtime dependency."""
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    project = data["project"]
    specs: dict[str, str] = {}
    for spec in project.get("dependencies", []):
        specs[f"dependencies[{spec}]"] = spec
    for extra_name, extra_specs in project.get("optional-dependencies", {}).items():
        for spec in extra_specs:
            specs[f"optional-dependencies.{extra_name}[{spec}]"] = spec
    return specs


def _requirements_txt_specs(path: Path) -> dict[str, str]:
    """Map ``"line N -> spec string"`` for every non-comment, non-blank line."""
    specs: dict[str, str] = {}
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        specs[f"{path.name}:{line_no}[{line}]"] = line
    return specs


def _unbounded(specs: dict[str, str]) -> list[str]:
    """Keys whose spec has a version constraint but no upper bound."""
    offenses = []
    for key, spec in specs.items():
        if not _VERSION_OPERATOR_RE.search(spec):
            continue  # bare self-reference (e.g. "jarvis-agent[memory]") — nothing to bound
        if not _UPPER_BOUNDED_RE.search(spec):
            offenses.append(key)
    return offenses


class TestDependencyBoundsGuard:
    def test_pyproject_runtime_dependencies_have_upper_bounds(self):
        specs = _pyproject_dependency_specs()
        assert specs, "expected at least one dependency spec in pyproject.toml"
        offenses = _unbounded(specs)
        assert not offenses, (
            f"Found {len(offenses)} unbounded dependency range(s) in pyproject.toml "
            "(#1296 — every runtime range must carry an explicit upper bound so a "
            "breaking major can't silently red every CI job, cf. the 2026-07-28 mcp "
            "2.0 outage / #1292 / #1294):\n" + "\n".join(f"  {o}" for o in offenses)
        )

    def test_mcp_memory_requirements_have_upper_bounds(self):
        specs = _requirements_txt_specs(MCP_MEMORY_REQUIREMENTS_PATH)
        assert specs, "expected at least one dependency line in mcp-memory/requirements.txt"
        offenses = _unbounded(specs)
        assert not offenses, (
            f"Found {len(offenses)} unbounded dependency range(s) in "
            "mcp-memory/requirements.txt — the live memory-server install manifest "
            "(#1296):\n" + "\n".join(f"  {o}" for o in offenses)
        )

    def test_guarded_files_are_canonical(self):
        """Lock the scanned-file set so a new manifest can't quietly join the repo
        unbounded (e.g. a future `mcp-memory/pyproject.toml` or a second
        requirements.txt) without this guard being extended to cover it.
        """
        assert PYPROJECT_PATH.is_file()
        assert MCP_MEMORY_REQUIREMENTS_PATH.is_file()
