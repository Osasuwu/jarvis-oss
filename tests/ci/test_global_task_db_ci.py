"""Meta-test pinning the DB-gated advancer CI wiring (#975).

The DB-gated tests in ``tests/reactive_core/test_global_task_advancer.py`` exercise the
advancer against a real Postgres: ``FOR UPDATE SKIP LOCKED`` claiming,
``ON CONFLICT (dedup_key) DO NOTHING`` dedup, and interval arithmetic. Before
#975 they ``pytest.skip()``-ed whenever ``DATABASE_URL`` was unset — which is
*always* the case in CI — so the suite went green without ever running them.
That is the exact "silent green" blind spot #957 warned about, one layer down.

The ``pytest-db`` job in ``.github/workflows/pytest.yml`` closes it by standing
up a Postgres service and running that file with ``DATABASE_URL`` set and
``REQUIRE_DB=1`` (which makes the fixture FAIL, not skip, if the DB ever goes
missing). This meta-test pins that wiring so a future edit can't quietly revert
to the silent skip:

1. **Config check** — the ``pytest-db`` job exists with the canonical name, has
   a Postgres service, sets ``DATABASE_URL``, bootstraps the schema from the
   REAL migration (not a re-implementation), runs ``REQUIRE_DB=1``, and targets
   the advancer test file.
2. **Logic check** — a pure-Python reimplementation of the fixture's
   skip-vs-fail-vs-connect decision rule, asserted against the three
   environment states.

#975's wiring is not (yet) a path-filtered guard nor a branch-protection merge
gate — the merge-gate decision is deferred to the owner. This meta-test follows
the #326 convention regardless, because the failure mode it guards against
("DB job written, DB job silently neutered, nobody notices") is identical.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest.yml"
BOOTSTRAP_SQL = REPO_ROOT / "tests" / "ci" / "global_task_schema_bootstrap.sql"

# Canonical names — branch protection (if it ever references this job) and any
# future path-filtered guard must use these exact strings.
CANONICAL_JOB_NAME = "pytest-db"
ADVANCER_TEST_FILE = "tests/reactive_core/test_global_task_advancer.py"
REAL_MIGRATION = "supabase/migrations/20260615120000_create_global_task_sources.sql"


def _load_workflow() -> dict:
    with WORKFLOW_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _job() -> dict:
    wf = _load_workflow()
    assert CANONICAL_JOB_NAME in wf["jobs"], (
        f"workflow must define the canonical {CANONICAL_JOB_NAME!r} job — "
        "renaming it without updating this test (and any branch-protection "
        "reference) silently drops the DB-gated coverage (#975)."
    )
    return wf["jobs"][CANONICAL_JOB_NAME]


# -- Config check ------------------------------------------------------------


class TestWorkflowConfigIntegrity:
    """Pin the load-bearing pieces of the pytest-db job."""

    def test_has_postgres_service(self) -> None:
        services = _job().get("services", {})
        assert "postgres" in services, "pytest-db must run a postgres service"
        image = str(services["postgres"].get("image", ""))
        assert image.startswith("postgres"), f"expected a postgres image, got {image!r}"

    def test_database_url_set(self) -> None:
        env = _job().get("env", {})
        assert "DATABASE_URL" in env, (
            "DATABASE_URL must be set on the job — without it the advancer tests "
            "skip instead of run, which is the blind spot #975 closes."
        )

    def test_bootstrap_applies_real_migration(self) -> None:
        """The job must apply the REAL migration, not a re-implementation, so the
        tests exercise production DDL (constraints, RLS, indexes)."""
        steps = _job().get("steps", [])
        run_text = "\n".join(str(s.get("run", "")) for s in steps)
        # M2 (round 2): require `psql -f <migration>`, not merely the path
        # appearing somewhere (a comment or echo mentioning the file would pass a
        # bare substring check while the migration was never actually applied).
        assert re.search(r"-f\s+" + re.escape(REAL_MIGRATION), run_text), (
            f"bootstrap must apply {REAL_MIGRATION} via `psql -f` — applying a "
            "hand-rolled table copy (or merely naming the file) would let the "
            "schema drift from production silently."
        )
        assert re.search(r"-f\s+\S*global_task_schema_bootstrap\.sql", run_text), (
            "bootstrap must seed the legacy events table + roles via "
            "`psql -f tests/ci/global_task_schema_bootstrap.sql` before the migration."
        )
        # Order matters: the migration's `do $$ ... $$` guard raises if the
        # events table is absent, and it references the roles the bootstrap
        # creates. A reversed psql sequence passes a flat `in` check but fails
        # at runtime — pin the order explicitly.
        boot_pos = run_text.index("global_task_schema_bootstrap.sql")
        mig_pos = run_text.index(REAL_MIGRATION)
        assert boot_pos < mig_pos, (
            "global_task_schema_bootstrap.sql must be applied BEFORE "
            f"{REAL_MIGRATION} — the migration depends on the events table and "
            "roles the bootstrap creates."
        )

    def test_require_db_enforced(self) -> None:
        """REQUIRE_DB=1 turns a missing DATABASE_URL from a silent skip into a
        hard failure — the anti-regression latch for this whole issue.

        Narrowed (M1, round 2): REQUIRE_DB must be set on the *same* step that
        actually runs the advancer test file. A job-level or unrelated-step
        REQUIRE_DB would satisfy a loose ``any(...)`` while the pytest invocation
        ran without it — the latch would be cosmetically present but inert.
        """
        steps = _job().get("steps", [])
        require_db_on_advancer_step = any(
            ADVANCER_TEST_FILE in str(s.get("run", ""))
            and str(s.get("env", {}).get("REQUIRE_DB", "")).strip() not in ("", "0")
            for s in steps
        )
        assert require_db_on_advancer_step, (
            "the step that runs the advancer test file must itself set REQUIRE_DB "
            "so a future change that drops the Postgres service fails CI loudly "
            "instead of reverting to silent skip."
        )

    def test_targets_advancer_file(self) -> None:
        steps = _job().get("steps", [])
        run_text = "\n".join(str(s.get("run", "")) for s in steps)
        assert ADVANCER_TEST_FILE in run_text, (
            f"the DB job must invoke pytest on {ADVANCER_TEST_FILE}."
        )

    def test_bootstrap_sql_exists(self) -> None:
        assert BOOTSTRAP_SQL.is_file(), (
            "tests/ci/global_task_schema_bootstrap.sql must exist — the workflow "
            "psql -f step references it."
        )


# -- Logic check -------------------------------------------------------------


def _fixture_decision(database_url: str | None, require_db: str | None) -> str:
    """Pure-Python mirror of the db_connection fixture's gating rule.

    Returns one of: ``"connect"`` (DATABASE_URL present → real connection),
    ``"fail"`` (REQUIRE_DB set but DATABASE_URL missing → hard error), or
    ``"skip"`` (neither → clean skip). Mirrors the branch in
    tests/reactive_core/test_global_task_advancer.py::db_connection — kept in sync by hand
    because the fixture imports psycopg at call time and can't be invoked here.
    """
    if database_url:
        return "connect"
    # Match the fixture exactly: REQUIRE_DB="0" (and "") is OFF, not a hard fail.
    # A bare `if require_db` would treat the string "0" as truthy and diverge.
    if require_db and str(require_db).strip() not in ("", "0"):
        return "fail"
    return "skip"


class TestFixtureGatingLogic:
    """The three environment states the REQUIRE_DB latch distinguishes."""

    def test_connects_when_database_url_present(self) -> None:
        assert _fixture_decision("postgres://x", None) == "connect"
        assert _fixture_decision("postgres://x", "1") == "connect"

    def test_fails_when_require_db_but_no_url(self) -> None:
        # This is the CI state: REQUIRE_DB=1 in the job, DATABASE_URL would only
        # be missing if the service were dropped — must fail, not skip.
        assert _fixture_decision(None, "1") == "fail"

    def test_skips_when_neither(self) -> None:
        # Local dev with no Postgres — clean skip, not a failure.
        assert _fixture_decision(None, None) == "skip"

    def test_require_db_zero_is_off(self) -> None:
        # REQUIRE_DB="0" (and "") is OFF in the real fixture — a bare-truthiness
        # mirror would wrongly "fail" here. Pins the sync the docstring claims.
        assert _fixture_decision(None, "0") == "skip"
        assert _fixture_decision(None, "") == "skip"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
