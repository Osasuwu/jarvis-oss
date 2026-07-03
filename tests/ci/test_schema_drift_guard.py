"""Meta-test for .github/workflows/schema-drift-check.yml (#326).

The pattern this test enforces — **every path-filtered CI guard ships with
a co-located fixture test that proves it blocks what it should** — is the
response to the failure mode of #289/#310/#311:

  PR #289 pointed the guard at `supabase/schema.sql`, the canonical file
  is `mcp-memory/schema.sql`. The guard silently passed for a full sprint
  on PRs that should have been blocked. There was no meta-protection
  against "guard written, guard wrong, nobody notices."

Two dimensions covered here; both are required for a guard to be trusted:

1. **Config check** — the `paths:` filter in the workflow YAML must
   reference the canonical schema path. If someone renames or moves
   `mcp-memory/schema.sql`, the guard must move with it.
2. **Logic check** — three scenarios (schema+migration, schema-only,
   unrelated change) asserted against a pure-Python reimplementation
   of the workflow's JS decision rule.

The logic reimplementation is intentionally a parallel copy rather than
an import — the workflow runs github-script (JS) on GitHub's runners.
Drift between `_decide()` and the workflow is still possible, but the
config check anchors the most load-bearing invariant (watched path =
canonical path), which is the exact class of bug that motivated #326.

Convention for future guards: `.github/workflows/X-guard.yml` =>
`tests/ci/test_X_guard.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "schema-drift-check.yml"

CANONICAL_SCHEMA_PATH = "mcp-memory/schema.sql"
MIGRATIONS_PREFIX = "supabase/migrations/"


# -- Config check ------------------------------------------------------------


def _load_workflow() -> dict:
    with WORKFLOW_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestWorkflowConfigIntegrity:
    """Anchor the most load-bearing invariant: the guard watches the right path.

    If the canonical schema path changes, this test forces the workflow
    to be updated in the same PR — or a reviewer sees red CI.
    """

    def test_workflow_file_exists(self):
        assert WORKFLOW_PATH.exists(), (
            f"Expected guard workflow at {WORKFLOW_PATH.relative_to(REPO_ROOT)}"
        )

    def test_triggers_on_pull_request(self):
        wf = _load_workflow()
        # PyYAML parses the `on:` key as the Python boolean `True` (YAML 1.1
        # treats the literal `on` as a synonym for true). Accept either key
        # so the test survives either yaml-lib behavior.
        triggers = wf.get("on") or wf.get(True)
        assert triggers is not None, "Workflow must declare `on:` triggers"
        assert "pull_request" in triggers, "Guard must run on pull_request events"

    def test_paths_filter_includes_canonical_schema(self):
        wf = _load_workflow()
        triggers = wf.get("on") or wf.get(True)
        pr_filter = triggers["pull_request"]
        paths = pr_filter.get("paths", [])
        assert CANONICAL_SCHEMA_PATH in paths, (
            f"Guard must watch `{CANONICAL_SCHEMA_PATH}` — the canonical schema. "
            f"Current paths: {paths}. See #289/#310/#311 for why this matters."
        )

    def test_paths_filter_includes_migrations_dir(self):
        """A migration-file change should also trigger the guard (safety net:
        e.g. reviewer deletes migration without reverting schema)."""
        wf = _load_workflow()
        triggers = wf.get("on") or wf.get(True)
        paths = triggers["pull_request"].get("paths", [])
        assert any(p.startswith(MIGRATIONS_PREFIX) for p in paths), (
            f"Guard must also watch `{MIGRATIONS_PREFIX}**`. Current paths: {paths}"
        )

    def test_no_wrong_legacy_path(self):
        """Regression test for the original bug — guard previously watched
        `supabase/schema.sql`, which is not the canonical location."""
        wf = _load_workflow()
        triggers = wf.get("on") or wf.get(True)
        paths = triggers["pull_request"].get("paths", [])
        assert "supabase/schema.sql" not in paths, (
            "supabase/schema.sql is NOT the canonical path — "
            "canonical is mcp-memory/schema.sql (#310)."
        )


# -- Logic check -------------------------------------------------------------


def _decide(files: list[dict]) -> str:
    """Pure-Python reimplementation of the guard's JS decision rule.

    Mirrors .github/workflows/schema-drift-check.yml github-script body.
    Returns one of: "skip", "fail", "pass".

    Keep this function in sync with the workflow. The config tests above
    lock down the `paths:` filter; this function locks down the decision
    logic. If the workflow changes its logic, update this function and
    add a corresponding scenario.
    """
    schema_changed = any(
        f["filename"] == CANONICAL_SCHEMA_PATH
        and f["status"] in ("modified", "added", "changed")
        for f in files
    )
    if not schema_changed:
        return "skip"

    migration_added = any(
        f["filename"].startswith(MIGRATIONS_PREFIX) and f["status"] == "added"
        for f in files
    )
    return "pass" if migration_added else "fail"


class TestGuardLogic:
    """Scenarios: the guard must block what it claims to block."""

    def test_skips_when_schema_unchanged(self):
        files = [{"filename": "README.md", "status": "modified"}]
        assert _decide(files) == "skip"

    def test_blocks_schema_edit_without_migration(self):
        """The exact failure mode from #284 — schema edited, no migration,
        broken in prod. Guard must FAIL this PR."""
        files = [{"filename": CANONICAL_SCHEMA_PATH, "status": "modified"}]
        assert _decide(files) == "fail"

    def test_passes_with_paired_migration(self):
        files = [
            {"filename": CANONICAL_SCHEMA_PATH, "status": "modified"},
            {"filename": "supabase/migrations/20260424_add_thing.sql", "status": "added"},
        ]
        assert _decide(files) == "pass"

    def test_blocks_schema_added_without_migration(self):
        """Edge case: brand-new schema file — still needs a migration."""
        files = [{"filename": CANONICAL_SCHEMA_PATH, "status": "added"}]
        assert _decide(files) == "fail"

    def test_modified_migration_alone_does_not_pass(self):
        """Modifying an existing migration (without schema change) is skip-territory —
        the guard only fires on schema changes. This locks down current behavior."""
        files = [{"filename": "supabase/migrations/20260101_old.sql", "status": "modified"}]
        assert _decide(files) == "skip"

    @pytest.mark.parametrize(
        "files,expected",
        [
            ([], "skip"),
            ([{"filename": "docs/notes.md", "status": "added"}], "skip"),
            (
                [
                    {"filename": "docs/notes.md", "status": "added"},
                    {"filename": CANONICAL_SCHEMA_PATH, "status": "modified"},
                ],
                "fail",
            ),
            (
                [
                    {"filename": CANONICAL_SCHEMA_PATH, "status": "modified"},
                    {"filename": "supabase/migrations/new.sql", "status": "added"},
                    {"filename": "other.py", "status": "modified"},
                ],
                "pass",
            ),
        ],
    )
    def test_mixed_file_sets(self, files, expected):
        assert _decide(files) == expected
