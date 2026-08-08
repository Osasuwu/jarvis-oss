"""Guard for #1328 — SOUL.md must be delivered by @import, not a SessionStart cat.

Mirrors `test_doctrine_pointers_guard.py`'s pattern for `@DOCTRINE.md` (#1315):
a bare `@SOUL.md` import line in `.claude-userlevel/CLAUDE.md` is how identity
content reaches every session (fresh, `--resume`d, post-compaction) — a
SessionStart hook only fires on `startup`/`compact` matchers and silently
skips `--resume`, which was the gap #1328 closes.

Two checks pinned here:
  - the `@SOUL.md` import line exists in CLAUDE.md outside any code span
  - the `soul` manifest group (config/SOUL.md -> SOUL.md) is present and enabled

The "no SessionStart hook cats a static file" assertion lives in
tests/infrastructure/test_installer.py::test_userlevel_settings_no_longer_cats_soul
— not duplicated here to avoid two tests asserting the same fact.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ._md_helpers import find_bare_imports

REPO_ROOT = Path(__file__).resolve().parents[2]
USERLEVEL_DIR = REPO_ROOT / ".claude-userlevel"
CLAUDE_MD_PATH = USERLEVEL_DIR / "CLAUDE.md"
SOUL_MD_PATH = REPO_ROOT / "config" / "SOUL.md"
MANIFEST_PATH = REPO_ROOT / "install-manifest.yaml"


class TestSoulImport:
    def test_claude_md_exists(self):
        assert CLAUDE_MD_PATH.exists(), f"missing {CLAUDE_MD_PATH}"

    def test_soul_md_exists(self):
        assert SOUL_MD_PATH.exists(), f"missing {SOUL_MD_PATH}"

    def test_bare_import_line_outside_code_span(self):
        """#1426: assert the import FORM, not the substring.

        The previous assertion (`"@SOUL.md" in stripped`) passed against a
        purely decorative mid-prose mention — which is what
        `.claude-userlevel/CLAUDE.md` actually carried, so SOUL.md never
        reached any session while the guard stayed green for months. A guard
        that checks a marker is *present* does not check the marker *works*.
        """
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        paths = [path for _, path in find_bare_imports(text)]
        assert "SOUL.md" in paths, (
            "expected a BARE, line-start `@SOUL.md` import in "
            ".claude-userlevel/CLAUDE.md (a line whose entire content is the "
            f"import), outside any code span or fence. Found bare imports: {paths}. "
            "A mid-prose `@SOUL.md` mention does not count: the two mid-prose "
            "imports in this file failed to load their targets even though both "
            "targets existed on disk (#1426)."
        )

    def test_every_bare_import_target_is_installed(self):
        """A bare import whose target is missing loads silently as nothing.

        For the user-level file, "exists" means *installed by the manifest*:
        `@SOUL.md` resolves against `~/.claude/` at runtime, not against the
        repo, where the source lives at `config/SOUL.md`.
        """
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        installed = {
            f.get("dest")
            for g in manifest.get("groups", [])
            if g.get("enabled") is True
            for f in g.get("files", [])
        }
        for lineno, path in find_bare_imports(text):
            assert path in installed, (
                f".claude-userlevel/CLAUDE.md:{lineno} imports `@{path}`, but no "
                f"enabled install-manifest group installs a file with that dest. "
                f"The import would load as empty. Installed dests: {sorted(installed)}"
            )


class TestManifestCoverage:
    @pytest.fixture(scope="class")
    def manifest(self):
        assert MANIFEST_PATH.exists(), f"missing {MANIFEST_PATH}"
        return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_soul_covered_by_install_manifest(self, manifest):
        groups = manifest.get("groups", [])
        soul_groups = [
            g for g in groups if any(f.get("source") == "config/SOUL.md" for f in g.get("files", []))
        ]
        assert soul_groups, (
            "install-manifest.yaml has no group installing config/SOUL.md — "
            "SOUL.md would never reach ~/.claude/ on `install.ps1 -Apply`"
        )

    def test_soul_group_enabled(self, manifest):
        groups = manifest.get("groups", [])
        soul_groups = [g for g in groups if g.get("id") == "soul"]
        assert soul_groups, "no manifest group with id 'soul'"
        assert soul_groups[0].get("enabled") is True, "soul manifest group is not enabled"
