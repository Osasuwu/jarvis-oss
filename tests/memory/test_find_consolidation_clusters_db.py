"""DB-gated tests for find_consolidation_clusters (#1187).

The rewritten RPC (supabase/migrations/20260715130000_rewrite_find_
consolidation_clusters.sql) is plpgsql running inside Postgres — HNSW LATERAL
probes, temp-table label propagation, cap-10 truncation — none of which a
pure-Python unit test can exercise (those live in
tests/memory/test_consolidation_merge_plan.py, which covers the pure-Python
canonical_project derivation instead). This file pins the AC 7 semantics
against a real pgvector-enabled Postgres:

  - partition isolation ((type, project_key) homogeneity)
  - transitive merge (A~B~C one cluster even when sim(A,C) < threshold)
  - cap-10 truncation (ordered by updated_at desc)
  - dead-row exclusion (superseded/expired/deleted/valid_to-lapsed rows
    appear in neither anchors nor members)
  - disjointness (no memory_id in more than one cluster)

Run by the `pytest-db-pgvector` job in .github/workflows/pytest.yml, which
bootstraps tests/ci/pgvector_schema_bootstrap.sql and applies the real
migration on top. Skips cleanly with no DATABASE_URL (local/non-DB runs);
REQUIRE_DB=1 (set by that job) turns a missing DATABASE_URL into a hard
failure instead of a silent skip, mirroring db_connection in
tests/reactive_core/test_global_task_advancer.py (#975).

Two-dimensional embedding trick: each synthetic embedding is a unit vector
with only its first two of 512 components non-zero, `[cos(theta), sin(theta),
0, ...]`. Cosine similarity between two such vectors reduces to
`cos(theta_a - theta_b)`, so similarity is fully controlled by the angle
delta between two memories — no need for real embeddings to pin exact
threshold behavior.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


DIM = 512
SIM_THRESHOLD = 0.80
MIN_CLUSTER_SIZE = 3
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _vec(angle_deg: float, dim: int = DIM) -> str:
    theta = math.radians(angle_deg)
    v = [0.0] * dim
    v[0] = math.cos(theta)
    v[1] = math.sin(theta)
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


@pytest.fixture
def db_connection() -> Any:
    """Postgres connection, rolled back (never committed) after each test.

    Mirrors tests/reactive_core/test_global_task_advancer.py::db_connection
    (REQUIRE_DB fail-vs-skip semantics), but additionally never commits — each
    test gets a fresh session and its inserts are discarded at teardown so
    synthetic memories from one test can never appear as HNSW neighbors in
    another (the bare probe in find_consolidation_clusters has no partition
    filter and would otherwise leak across tests running against a shared DB
    within one CI job).
    """
    db_url = os.environ.get("DATABASE_URL")
    require_db = os.environ.get("REQUIRE_DB", "").strip() not in ("", "0")
    if not db_url:
        if require_db:
            pytest.fail(
                "REQUIRE_DB is set but DATABASE_URL is missing — the DB-gated "
                "find_consolidation_clusters tests must run against real "
                "Postgres in this job, not silently skip (#1187)."
            )
        pytest.skip("DATABASE_URL not set (no Postgres connection available)")

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        pytest.skip("psycopg not installed")

    try:
        conn = psycopg.connect(db_url, row_factory=dict_row, autocommit=False)
    except Exception as e:  # noqa: BLE001 — connection failure must fail loudly, not skip
        pytest.fail(f"could not connect to DATABASE_URL: {type(e).__name__}")

    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _insert_memory(
    conn: Any,
    *,
    name: str,
    mtype: str = "project",
    project: str = "test-proj",
    angle: float = 0.0,
    updated_at: datetime = BASE_TIME,
    expired_at: datetime | None = None,
    superseded_by: str | None = None,
    deleted_at: datetime | None = None,
    valid_to: datetime | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into memories
              (type, project, name, content, embedding, updated_at,
               expired_at, superseded_by, deleted_at, valid_to)
            values
              (%(type)s, %(project)s, %(name)s, %(content)s, %(embedding)s::vector,
               %(updated_at)s, %(expired_at)s, %(superseded_by)s, %(deleted_at)s, %(valid_to)s)
            returning id
            """,
            {
                "type": mtype,
                "project": project,
                "name": name,
                "content": name,
                "embedding": _vec(angle),
                "updated_at": updated_at,
                "expired_at": expired_at,
                "superseded_by": superseded_by,
                "deleted_at": deleted_at,
                "valid_to": valid_to,
            },
        )
        return str(cur.fetchone()["id"])


def _call_rpc(
    conn: Any, min_cluster_size: int = MIN_CLUSTER_SIZE, sim_threshold: float = SIM_THRESHOLD
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "select * from find_consolidation_clusters(%s, %s)",
            (min_cluster_size, sim_threshold),
        )
        return cur.fetchall()


def _by_cluster(rows: list[dict]) -> dict[int, list[dict]]:
    clusters: dict[int, list[dict]] = {}
    for row in rows:
        clusters.setdefault(row["cluster_id"], []).append(row)
    return clusters


def _names_in_cluster(rows: list[dict]) -> set[str]:
    return {row["memory_name"] for row in rows}


class TestPartitionIsolation:
    def test_same_project_different_type_do_not_merge(self, db_connection: Any) -> None:
        # Identical embedding (sim=1.0) but different type — must stay in
        # separate clusters despite trivially passing the similarity check.
        for i in range(MIN_CLUSTER_SIZE):
            _insert_memory(db_connection, name=f"proj-{i}", mtype="project", project="acme")
        for i in range(MIN_CLUSTER_SIZE):
            _insert_memory(db_connection, name=f"feedback-{i}", mtype="feedback", project="acme")

        rows = _call_rpc(db_connection)
        clusters = _by_cluster(rows)

        assert len(clusters) == 2, f"expected 2 disjoint clusters, got {len(clusters)}"
        for members in clusters.values():
            types = {m["memory_type"] for m in members}
            assert len(types) == 1, f"cluster mixes types: {types}"

    def test_same_type_different_project_do_not_merge(self, db_connection: Any) -> None:
        for i in range(MIN_CLUSTER_SIZE):
            _insert_memory(db_connection, name=f"jarvis-{i}", mtype="project", project="jarvis")
        for i in range(MIN_CLUSTER_SIZE):
            _insert_memory(db_connection, name=f"redrobot-{i}", mtype="project", project="redrobot")

        rows = _call_rpc(db_connection)
        clusters = _by_cluster(rows)

        assert len(clusters) == 2, f"expected 2 disjoint clusters, got {len(clusters)}"
        name_sets = [_names_in_cluster(members) for members in clusters.values()]
        assert all(n.issubset({f"jarvis-{i}" for i in range(3)}) or
                    n.issubset({f"redrobot-{i}" for i in range(3)}) for n in name_sets), (
            f"a cluster mixed projects: {name_sets}"
        )


class TestTransitiveMerge:
    def test_a_b_c_one_cluster_when_a_c_below_threshold(self, db_connection: Any) -> None:
        # sim(A,B) = sim(B,C) = cos(30deg) ~= 0.866 >= 0.80 (direct edges)
        # sim(A,C) = cos(60deg) = 0.5 < 0.80 (no direct edge, only transitive)
        _insert_memory(db_connection, name="A", angle=0)
        _insert_memory(db_connection, name="B", angle=30)
        _insert_memory(db_connection, name="C", angle=60)

        rows = _call_rpc(db_connection)
        clusters = _by_cluster(rows)

        assert len(clusters) == 1, f"expected A/B/C to form one component, got {clusters}"
        (members,) = clusters.values()
        assert _names_in_cluster(members) == {"A", "B", "C"}


class TestCapTruncation:
    def test_component_over_ten_truncates_to_most_recent_ten(self, db_connection: Any) -> None:
        # 11 identical-embedding memories (all mutually sim=1.0) form one
        # component of size 11 — must truncate to the 10 most recently
        # updated, ordered by updated_at desc.
        names_oldest_to_newest = [f"m{i}" for i in range(11)]
        for i, name in enumerate(names_oldest_to_newest):
            _insert_memory(db_connection, name=name, angle=0, updated_at=BASE_TIME + timedelta(days=i))

        rows = _call_rpc(db_connection)
        clusters = _by_cluster(rows)

        assert len(clusters) == 1
        (members,) = clusters.values()
        assert len(members) == 10, f"expected cap-10 truncation, got {len(members)} members"

        kept_names = _names_in_cluster(members)
        oldest = names_oldest_to_newest[0]
        assert oldest not in kept_names, "the single oldest member must be the one truncated"
        assert kept_names == set(names_oldest_to_newest[1:])


class TestDeadRowExclusion:
    def test_superseded_expired_deleted_and_lapsed_rows_are_excluded(
        self, db_connection: Any
    ) -> None:
        live_names = {f"live-{i}" for i in range(MIN_CLUSTER_SIZE)}
        for name in live_names:
            _insert_memory(db_connection, name=name, angle=0)

        # anchor sits far from the live cluster (sim(0, 90) = 0 < threshold) so it
        # cannot join by its own similarity — the only thing that could pull it in
        # is the dead row's superseded_by pointer, which must NOT happen.
        anchor_id = _insert_memory(db_connection, name="anchor-for-supersede", angle=90)
        _insert_memory(db_connection, name="dead-superseded", angle=0, superseded_by=anchor_id)
        _insert_memory(
            db_connection, name="dead-expired", angle=0, expired_at=BASE_TIME + timedelta(days=1)
        )
        _insert_memory(
            db_connection, name="dead-deleted", angle=0, deleted_at=BASE_TIME + timedelta(days=1)
        )
        _insert_memory(
            db_connection,
            name="dead-lapsed",
            angle=0,
            valid_to=BASE_TIME - timedelta(days=1),
        )

        rows = _call_rpc(db_connection)
        clusters = _by_cluster(rows)

        assert len(clusters) == 1, f"expected exactly one live cluster, got {clusters}"
        (members,) = clusters.values()
        kept_names = _names_in_cluster(members)

        assert kept_names == live_names, f"dead rows leaked into the cluster: {kept_names}"
        assert "anchor-for-supersede" not in kept_names, (
            "anchor-for-supersede is below min_cluster_size on its own and must not "
            "be pulled in just because a dead row points at it"
        )


class TestDisjointness:
    def test_no_memory_id_appears_in_more_than_one_cluster(self, db_connection: Any) -> None:
        # Two far-apart components in the same partition (sim(0, 180) = -1,
        # well below threshold) plus the transitive-merge trio, all inserted
        # together — a regression to the old overlapping anchor-star
        # clustering would show the same id under multiple cluster_ids.
        for i in range(MIN_CLUSTER_SIZE):
            _insert_memory(db_connection, name=f"near-{i}", angle=0)
        for i in range(MIN_CLUSTER_SIZE):
            _insert_memory(db_connection, name=f"far-{i}", angle=180)

        rows = _call_rpc(db_connection)

        seen: dict[str, int] = {}
        for row in rows:
            mid = str(row["memory_id"])
            assert mid not in seen or seen[mid] == row["cluster_id"], (
                f"memory {mid} appears in clusters {seen.get(mid)} and {row['cluster_id']}"
            )
            seen[mid] = row["cluster_id"]

        assert len(_by_cluster(rows)) == 2
