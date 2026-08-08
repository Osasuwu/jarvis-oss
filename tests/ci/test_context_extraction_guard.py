"""Guard for #1417 — the extracted Invariants must be delivered by @import.

Mirrors `test_soul_import_guard.py` / `test_doctrine_pointers_guard.py`'s
pattern for `@SOUL.md` (#1328) / `@DOCTRINE.md` (#1315): a bare `@import`
line in the project's root `CLAUDE.md` is how this content reaches every
session, bypassing the SessionStart hook's budget-constrained assembler
entirely (the assembler used to drop `project_context` in 47% of sessions —
see CONTEXT.md → *Context delivery*).

Unlike SOUL.md/DOCTRINE.md, this is project-repo content (not user-level), so
there is no install-manifest coverage to check — the whole jarvis repo ships
as-is via `git clone`/`git pull`, and CLAUDE.md is already read by the harness
in every jarvis session.

#1417 extracted two files; #1418 retired the second. `docs/context/glossary-index.md`
was a hand-maintained category+count snapshot of `CONTEXT.md`'s Glossary, and an
index of where to look does not need to be always-loaded to be findable — a
one-line pull pointer in CLAUDE.md replaced it. Only `invariants.md` still rides
an `@import`, so only it is pinned here.

Three checks pinned here:
  - the file exists and carries its unique import-marker string
  - its bare `@import` line exists in root CLAUDE.md, outside any code span
  - `scripts/session-context.py` no longer defines the retired assembler path
    (`_load_project_context`) — the whole point of #1417 is that this content
    no longer rides the budget-constrained push
"""

from __future__ import annotations

from pathlib import Path

from ._md_helpers import find_bare_imports

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
INVARIANTS_MD_PATH = REPO_ROOT / "docs" / "context" / "invariants.md"
GLOSSARY_INDEX_MD_PATH = REPO_ROOT / "docs" / "context" / "glossary-index.md"
SESSION_CONTEXT_PATH = REPO_ROOT / "scripts" / "session-context.py"


class TestExtractedFilesExist:
    def test_invariants_md_exists(self):
        assert INVARIANTS_MD_PATH.exists(), f"missing {INVARIANTS_MD_PATH}"

    def test_invariants_md_has_unique_marker(self):
        text = INVARIANTS_MD_PATH.read_text(encoding="utf-8")
        assert "<!-- jarvis-context-import-marker: invariants-md -->" in text


class TestImportLines:
    def test_claude_md_exists(self):
        assert CLAUDE_MD_PATH.exists(), f"missing {CLAUDE_MD_PATH}"

    def test_bare_invariants_import_line_outside_code_span(self):
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        paths = [path for _, path in find_bare_imports(text)]
        assert "docs/context/invariants.md" in paths, (
            "expected a BARE, line-start `@docs/context/invariants.md` import in root "
            f"CLAUDE.md, outside any code span or fence. Found bare imports: {paths}. "
            "A mid-prose mention does not count (#1417, form asserted per #1426)."
        )

    def test_glossary_index_stays_retired(self):
        """#1418 evicted the Glossary category index from the always-loaded layer.

        Reinstating it — as a file plus an `@import`, or as any other bare
        import of that path — silently re-adds ~1.1 KB paid every session, again
        after every compaction, and N+1 times per fan-out. The replacement is a
        one-line pull pointer at `CONTEXT.md` -> `## Glossary`; if the index is
        ever genuinely needed again, that is a fixture-and-decision change, not
        a quiet re-import.
        """
        assert not GLOSSARY_INDEX_MD_PATH.exists(), (
            f"{GLOSSARY_INDEX_MD_PATH} was retired by #1418 — reinstating the "
            "always-loaded category index needs a fresh record_decision, not a "
            "silent restore"
        )
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        paths = [path for _, path in find_bare_imports(text)]
        assert "docs/context/glossary-index.md" not in paths, (
            "root CLAUDE.md must not bare-import the retired glossary index "
            f"(#1418). Found bare imports: {paths}."
        )

    def test_every_bare_import_target_exists_on_disk(self):
        """A bare import to a missing file loads silently as nothing.

        Project `CLAUDE.md` imports resolve relative to the repo root, so this
        one can be checked directly (unlike the user-level file, whose targets
        resolve against the installed `~/.claude/` layout).
        """
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        for lineno, path in find_bare_imports(text):
            resolved = (CLAUDE_MD_PATH.parent / path).resolve()
            assert resolved.is_file(), (
                f"CLAUDE.md:{lineno} imports `@{path}`, which does not exist at "
                f"{resolved} — the import would load as empty"
            )


class TestRetiredAssemblerPath:
    def test_load_project_context_removed(self):
        text = SESSION_CONTEXT_PATH.read_text(encoding="utf-8")
        assert "_load_project_context" not in text, (
            "scripts/session-context.py must not define/reference "
            "_load_project_context — #1417 retired the budget-constrained "
            "CONTEXT.md push in favor of @import delivery"
        )

    def test_priority_context_push_removed(self):
        text = SESSION_CONTEXT_PATH.read_text(encoding="utf-8")
        assert "_PRIORITY_CONTEXT_PUSH" not in text, (
            "scripts/session-context.py must not define/reference "
            "_PRIORITY_CONTEXT_PUSH — its priority slot was removed by #1417"
        )
