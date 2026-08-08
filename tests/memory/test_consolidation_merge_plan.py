"""Unit tests for scripts/consolidation-merge-plan.py — project derivation (#1187).

find_consolidation_clusters now partitions on (type, project_key), so clusters
are homogeneous by project the same way they're homogeneous by type. These
tests cover the fix that replaced the hardcoded `"canonical_project": "jarvis"`
in build_payload() with a value derived from actual cluster membership.

No network/DB needed — group_clusters/_cluster_project/build_payload are pure
functions over RPC rows + member detail dicts.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "consolidation-merge-plan.py"
)

spec = importlib.util.spec_from_file_location("consolidation_merge_plan", SCRIPT_PATH)
assert spec and spec.loader
merge_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge_plan)


def _rpc_row(cluster_id, memory_id, name, mtype, sim, updated_at):
    return {
        "cluster_id": cluster_id,
        "memory_id": memory_id,
        "memory_name": name,
        "memory_type": mtype,
        "similarity": sim,
        "updated_at": updated_at,
    }


def _details(project, **overrides):
    row = {
        "description": "",
        "tags": [],
        "content": "",
        "expired_at": None,
        "valid_to": None,
        "superseded_by": None,
        "deleted_at": None,
        "project": project,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# group_clusters — carries `project` per member, `projects` per cluster
# ---------------------------------------------------------------------------


class TestGroupClustersProject:
    def test_member_project_threaded_from_details(self):
        rpc_rows = [_rpc_row(1, "m1", "note1", "project", 0.9, "2026-07-01T00:00:00Z")]
        details = {"m1": _details("redrobot")}
        clusters = merge_plan.group_clusters(rpc_rows, details)
        assert clusters[0]["members"][0]["project"] == "redrobot"

    def test_cluster_projects_aggregated_and_sorted(self):
        rpc_rows = [
            _rpc_row(1, "m1", "note1", "project", 0.9, "2026-07-01T00:00:00Z"),
            _rpc_row(1, "m2", "note2", "project", 0.85, "2026-07-02T00:00:00Z"),
        ]
        details = {"m1": _details("redrobot"), "m2": _details("redrobot")}
        clusters = merge_plan.group_clusters(rpc_rows, details)
        assert clusters[0]["projects"] == ["redrobot"]

    def test_dead_member_excluded_from_projects(self):
        rpc_rows = [
            _rpc_row(1, "m1", "note1", "project", 0.9, "2026-07-01T00:00:00Z"),
            _rpc_row(1, "m2", "note2", "project", 0.85, "2026-07-02T00:00:00Z"),
        ]
        details = {
            "m1": _details("redrobot"),
            "m2": _details("jarvis", expired_at="2026-07-03T00:00:00Z"),
        }
        clusters = merge_plan.group_clusters(rpc_rows, details)
        assert clusters[0]["projects"] == ["redrobot"]
        assert len(clusters[0]["members"]) == 1


# ---------------------------------------------------------------------------
# _cluster_project — mirrors _cluster_type
# ---------------------------------------------------------------------------


class TestClusterProject:
    def test_returns_the_shared_project(self):
        cluster = {"projects": ["redrobot"]}
        assert merge_plan._cluster_project(cluster) == "redrobot"

    def test_falls_back_to_jarvis_when_no_projects(self):
        cluster = {"projects": []}
        assert merge_plan._cluster_project(cluster) == "jarvis"

    def test_falls_back_to_jarvis_when_project_blank(self):
        # legacy rows with project = '' (project_key generated col: coalesce(project, ''))
        cluster = {"projects": [""]}
        assert merge_plan._cluster_project(cluster) == "jarvis"


# ---------------------------------------------------------------------------
# build_payload — canonical_project derived, not hardcoded
# ---------------------------------------------------------------------------


class TestBuildPayloadCanonicalProject:
    def test_uses_cluster_project_not_hardcoded_jarvis(self):
        cluster = {
            "cluster_id": 1,
            "types": ["project"],
            "projects": ["redrobot"],
            "members": [
                {"id": "m1", "name": "note1", "tags": []},
                {"id": "m2", "name": "note2", "tags": []},
            ],
        }
        plan = {
            "decision": "KEEP_DISTINCT",
            "supersede_ids": [],
            "canonical_name": None,
            "canonical_description": None,
            "canonical_content": None,
            "reasoning": "",
            "confidence": 0.5,
        }
        payload = merge_plan.build_payload(cluster, plan, "skill:consolidation:test")
        assert payload["canonical_project"] == "redrobot"

    def test_defaults_to_jarvis_for_legacy_projectless_cluster(self):
        cluster = {
            "cluster_id": 2,
            "types": ["project"],
            "projects": [],
            "members": [{"id": "m1", "name": "note1", "tags": []}],
        }
        plan = {
            "decision": "KEEP_DISTINCT",
            "supersede_ids": [],
            "canonical_name": None,
            "canonical_description": None,
            "canonical_content": None,
            "reasoning": "",
            "confidence": 0.5,
        }
        payload = merge_plan.build_payload(cluster, plan, "skill:consolidation:test")
        assert payload["canonical_project"] == "jarvis"
