"""Guard for #1315 — cross-repo doctrine pointers must not misdirect.

`.claude-userlevel/CLAUDE.md` (and its sibling `DOCTRINE.md`) load as
user-level memory in *every* Claude Code session on this device, including
non-jarvis repos. A bare `CONTEXT.md →
*Section*` pointer resolves fine in a jarvis session but silently
misdirects in any other repo, which has its own unrelated `CONTEXT.md`.
#1315 introduced `DOCTRINE.md` for shared norms and requalified every
jarvis-specific pointer as `` jarvis `CONTEXT.md` → *X* `` so it fails
visibly instead.

Three checks pinned here, per the issue's "Runnable check (repo floor)" AC:
  - no unqualified `CONTEXT.md →` pointer survives under `.claude-userlevel/`
  - the `@DOCTRINE.md` import line exists in CLAUDE.md outside any code span
  - `DOCTRINE.md` is covered by the install manifest
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ._md_helpers import find_bare_imports

REPO_ROOT = Path(__file__).resolve().parents[2]
USERLEVEL_DIR = REPO_ROOT / ".claude-userlevel"
CLAUDE_MD_PATH = USERLEVEL_DIR / "CLAUDE.md"
DOCTRINE_MD_PATH = USERLEVEL_DIR / "DOCTRINE.md"
MANIFEST_PATH = REPO_ROOT / "install-manifest.yaml"

# A `CONTEXT.md →` pointer only fails visibly in a foreign repo (instead of
# silently resolving against that repo's own unrelated CONTEXT.md) when it
# names "jarvis" explicitly right before it.
POINTER_RE = re.compile(r"(jarvis\s+)?`?CONTEXT\.md`?\s*→")


def find_unqualified_pointers(paths: list[Path]) -> list[str]:
    violations = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for m in POINTER_RE.finditer(text):
            if m.group(1):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append(
                f"{path.relative_to(REPO_ROOT)}:{line_no}: {lines[line_no - 1].strip()}"
            )
    return violations


class TestNoUnqualifiedContextPointers:
    def test_zero_unqualified_pointers_under_userlevel(self):
        assert USERLEVEL_DIR.exists(), f"missing {USERLEVEL_DIR}"
        md_files = sorted(USERLEVEL_DIR.rglob("*.md"))
        assert md_files, f"no markdown files found under {USERLEVEL_DIR}"
        violations = find_unqualified_pointers(md_files)
        assert not violations, (
            "unqualified `CONTEXT.md →` pointer(s) found under .claude-userlevel/ — "
            "these silently misdirect in non-jarvis sessions (e.g. redrobot, which has "
            "its own CONTEXT.md); qualify as `jarvis `CONTEXT.md` → ...` or move the "
            "content to DOCTRINE.md:\n" + "\n".join(violations)
        )


class TestDoctrineImport:
    def test_claude_md_exists(self):
        assert CLAUDE_MD_PATH.exists(), f"missing {CLAUDE_MD_PATH}"

    def test_doctrine_md_exists(self):
        assert DOCTRINE_MD_PATH.exists(), f"missing {DOCTRINE_MD_PATH}"

    def test_bare_import_line_outside_code_span(self):
        """#1426: same defect shape as the SOUL.md guard — assert the form.

        See `test_soul_import_guard.py` for the full rationale; both guards
        asserted a substring that a decorative mid-prose mention satisfied.
        """
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        paths = [path for _, path in find_bare_imports(text)]
        assert "DOCTRINE.md" in paths, (
            "expected a BARE, line-start `@DOCTRINE.md` import in "
            ".claude-userlevel/CLAUDE.md (a line whose entire content is the "
            f"import), outside any code span or fence. Found bare imports: {paths}. "
            "A mid-prose mention does not count (#1426)."
        )


class TestManifestCoverage:
    @pytest.fixture(scope="class")
    def manifest(self):
        assert MANIFEST_PATH.exists(), f"missing {MANIFEST_PATH}"
        return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_doctrine_covered_by_install_manifest(self, manifest):
        groups = manifest.get("groups", [])
        doctrine_groups = [
            g
            for g in groups
            if any(f.get("source") == ".claude-userlevel/DOCTRINE.md" for f in g.get("files", []))
        ]
        assert doctrine_groups, (
            "install-manifest.yaml has no group installing .claude-userlevel/DOCTRINE.md — "
            "DOCTRINE.md would never reach ~/.claude/ on `install.ps1 -Apply`"
        )

    def test_doctrine_group_enabled(self, manifest):
        groups = manifest.get("groups", [])
        doctrine_groups = [g for g in groups if g.get("id") == "doctrine"]
        assert doctrine_groups, "no manifest group with id 'doctrine'"
        assert doctrine_groups[0].get("enabled") is True, "doctrine manifest group is not enabled"
