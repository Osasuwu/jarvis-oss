"""Meta-test pinning the DB-gated find_consolidation_clusters CI wiring (#1187).

The DB-gated tests in tests/memory/test_find_consolidation_clusters_db.py
exercise the rewritten RPC against a real pgvector-enabled Postgres. Following
the #326 convention (and its direct precedent, tests/ci/test_global_task_db_ci.py
for the sibling `pytest-db` job): pin the job's config so a future edit can't
quietly drop the pgvector service, the REQUIRE_DB latch, or silently point the
job at a re-implemented schema instead of the real migration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest.yml"
BOOTSTRAP_SQL = REPO_ROOT / "tests" / "ci" / "pgvector_schema_bootstrap.sql"

CANONICAL_JOB_NAME = "pytest-db-pgvector"
CONSOLIDATION_TEST_FILE = "tests/memory/test_find_consolidation_clusters_db.py"
REAL_MIGRATION = "supabase/migrations/20260715130000_rewrite_find_consolidation_clusters.sql"


def _load_workflow() -> dict:
    with WORKFLOW_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _job() -> dict:
    wf = _load_workflow()
    assert CANONICAL_JOB_NAME in wf["jobs"], (
        f"workflow must define the canonical {CANONICAL_JOB_NAME!r} job — "
        "renaming it without updating this test silently drops the "
        "pgvector-gated find_consolidation_clusters coverage (#1187)."
    )
    return wf["jobs"][CANONICAL_JOB_NAME]


class TestWorkflowConfigIntegrity:
    def test_has_pgvector_service(self) -> None:
        services = _job().get("services", {})
        assert "postgres" in services, "pytest-db-pgvector must run a postgres service"
        image = str(services["postgres"].get("image", ""))
        assert image.startswith("pgvector/pgvector"), (
            f"expected a pgvector/pgvector image (plain postgres has no vector "
            f"extension), got {image!r}"
        )

    def test_database_url_set(self) -> None:
        env = _job().get("env", {})
        assert "DATABASE_URL" in env, (
            "DATABASE_URL must be set on the job — without it the "
            "find_consolidation_clusters tests skip instead of run."
        )

    def test_bootstrap_applies_real_migration(self) -> None:
        """The job must apply the REAL migration, not a re-implementation, so the
        tests exercise the production plpgsql (label propagation, cap-10
        truncation, HNSW probe)."""
        steps = _job().get("steps", [])
        run_text = "\n".join(str(s.get("run", "")) for s in steps)
        assert re.search(r"-f\s+" + re.escape(REAL_MIGRATION), run_text), (
            f"bootstrap must apply {REAL_MIGRATION} via `psql -f` — applying a "
            "hand-rolled function body (or merely naming the file) would let "
            "the tested function drift from production silently."
        )
        assert re.search(r"-f\s+\S*pgvector_schema_bootstrap\.sql", run_text), (
            "bootstrap must seed the vector extension + minimal memories table "
            "via `psql -f tests/ci/pgvector_schema_bootstrap.sql` before the migration."
        )
        boot_pos = run_text.index("pgvector_schema_bootstrap.sql")
        mig_pos = run_text.index(REAL_MIGRATION)
        assert boot_pos < mig_pos, (
            "pgvector_schema_bootstrap.sql must be applied BEFORE "
            f"{REAL_MIGRATION} — the migration's `create or replace function` "
            "references the memories table and vector type the bootstrap creates."
        )

    def test_require_db_enforced(self) -> None:
        """REQUIRE_DB=1 must be set on the step that actually runs the
        find_consolidation_clusters tests, not merely somewhere in the job —
        a job-level or unrelated-step REQUIRE_DB would pass a loose check
        while the pytest invocation ran without it."""
        steps = _job().get("steps", [])
        require_db_on_test_step = any(
            CONSOLIDATION_TEST_FILE in str(s.get("run", ""))
            and str(s.get("env", {}).get("REQUIRE_DB", "")).strip() not in ("", "0")
            for s in steps
        )
        assert require_db_on_test_step, (
            "the step that runs the find_consolidation_clusters test file must "
            "itself set REQUIRE_DB so a future change that drops the pgvector "
            "service fails CI loudly instead of reverting to silent skip."
        )

    def test_targets_consolidation_test_file(self) -> None:
        steps = _job().get("steps", [])
        run_text = "\n".join(str(s.get("run", "")) for s in steps)
        assert CONSOLIDATION_TEST_FILE in run_text, (
            f"the pgvector DB job must invoke pytest on {CONSOLIDATION_TEST_FILE}."
        )

    def test_bootstrap_sql_exists(self) -> None:
        assert BOOTSTRAP_SQL.is_file(), (
            "tests/ci/pgvector_schema_bootstrap.sql must exist — the workflow "
            "psql -f step references it."
        )


def _fixture_decision(database_url: str | None, require_db: str | None) -> str:
    """Pure-Python mirror of db_connection's gating rule in
    tests/memory/test_find_consolidation_clusters_db.py — kept in sync by hand,
    same as test_global_task_db_ci.py's twin for the sibling job."""
    if database_url:
        return "connect"
    if require_db and str(require_db).strip() not in ("", "0"):
        return "fail"
    return "skip"


class TestFixtureGatingLogic:
    def test_connects_when_database_url_present(self) -> None:
        assert _fixture_decision("postgres://x", None) == "connect"
        assert _fixture_decision("postgres://x", "1") == "connect"

    def test_fails_when_require_db_but_no_url(self) -> None:
        assert _fixture_decision(None, "1") == "fail"

    def test_skips_when_neither(self) -> None:
        assert _fixture_decision(None, None) == "skip"

    def test_require_db_zero_is_off(self) -> None:
        assert _fixture_decision(None, "0") == "skip"
        assert _fixture_decision(None, "") == "skip"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
